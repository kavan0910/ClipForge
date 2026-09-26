"""Stage entry points shared by the job runner and the CLI."""

from __future__ import annotations

import logging

from clipforge import jobs
from clipforge.config import Settings
from clipforge.curate.run import CurateOutcome, CurateParams, curate
from clipforge.curate.schema import Clip
from clipforge.llm.meter import CostMeter, UsageRecord
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, load_source
from clipforge.procs import CancelToken
from clipforge.signals.stage import Signals, compute_signals, load_signals
from clipforge.store import Project

log = logging.getLogger(__name__)

# Same defaults as the editor's "Render clip" button (RenderRequest in api/editor.py).
DEFAULT_RENDER_OPTS = {
    "fast": False, "template": None, "renderer": "auto", "captions": True,
    "cleanup": "light", "punch_in": 1.0, "upscale": None,
}  # fmt: skip


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
    if settings.auto_render_top:
        _auto_render_top(project, outcome.clips)
    return outcome


def _auto_render_top(project: Project, clips: list[Clip]) -> None:
    """Start rendering the single best-ranked clip from this run in the background, so a
    reviewable render (and, once it finishes, a ready-to-upload package for YouTube Shorts,
    Instagram Reels and TikTok) is waiting with no click needed. The user still approves or
    swaps the clip before posting anywhere. Best-effort: never fails clip selection."""
    if not clips:
        return
    top = max(clips, key=lambda c: c.rank_score)
    if top.status == "rejected" or project.path("clips", top.id, "out.mp4").exists():
        return
    try:
        jobs.start_render(project, top.id, dict(DEFAULT_RENDER_OPTS))
    except Exception:
        log.warning("auto-render of top clip %s failed to start", top.id, exc_info=True)
