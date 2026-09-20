"""Stage entry points shared by the job runner and the CLI."""

from __future__ import annotations

from clipforge.config import Settings
from clipforge.curate.run import CurateOutcome, CurateParams, curate
from clipforge.llm.meter import CostMeter, UsageRecord
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, load_source
from clipforge.procs import CancelToken
from clipforge.signals.stage import Signals, compute_signals, load_signals
from clipforge.store import Project


def load_transcript(project: Project) -> Transcript:
    return Transcript.model_validate_json(project.path("transcript", "transcript.json").read_text())


def signals_for(
    project: Project,
    source: Source,
    transcript: Transcript,
    reporter: Reporter,
    cancel: CancelToken,
) -> Signals:
    return compute_signals(project, source, transcript, reporter, cancel)


def curate_project(
    project: Project, settings: Settings, reporter: Reporter, cancel: CancelToken,
    params: CurateParams, recompute_signals: bool = False,
) -> CurateOutcome:  # fmt: skip
    """Signals (cached) then two-stage curation, emitting live cost events."""
    source, transcript = load_source(project), load_transcript(project)
    sig = None if recompute_signals else load_signals(project)
    if sig is None:
        sig = signals_for(project, source, transcript, reporter, cancel)

    def on_record(rec: UsageRecord, totals: dict) -> None:
        project.emit("cost", stage=rec.stage, model=rec.model, call_usd=round(rec.usd, 6), **totals)

    run_dir = project.path("curation", "runs")
    run_dir.mkdir(parents=True, exist_ok=True)
    meter = CostMeter(settings.max_job_cost_usd, on_record=on_record)
    outcome = curate(
        project, source, transcript, sig, settings, params,
        on_progress=lambda **d: reporter.progress(d.pop("stage", "curate"), d.pop("pct", None), **d),
        meter=meter,
    )  # fmt: skip
    project.emit("clips_ready", count=len(outcome.clips), run_id=outcome.run_id, **outcome.usage)
    return outcome
