"""Character-edit job: ingest -> proxy -> shot detection -> vision scan -> assemble one Clip.

No ASR/transcript stage runs in this mode; dialogue is irrelevant to finding a character on
screen, and skipping it saves real time on long episodes.
"""

from __future__ import annotations

import time
from pathlib import Path

from clipforge.character import scan as cscan
from clipforge.character.edit import build_clip
from clipforge.config import Settings
from clipforge.curate.run import save_clips
from clipforge.errors import ClipforgeError
from clipforge.llm.client import LLMClient
from clipforge.llm.meter import CostMeter, UsageRecord
from clipforge.pipeline import IngestOptions, Reporter, ensure_proxy, ingest
from clipforge.procs import CancelToken
from clipforge.providers.base import SourceProvider
from clipforge.signals.stage import precompute_visual
from clipforge.store import Project


def run_character_job(
    project: Project,
    provider: SourceProvider,
    settings: Settings,
    reporter: Reporter,
    cancel: CancelToken,
    opts: dict,
) -> int:
    character = (opts.get("character") or "").strip()
    if not character:
        raise ClipforgeError(
            "No character name was given.", "Type the character's name and try again."
        )
    if not settings.anthropic_api_key:
        raise ClipforgeError(
            "No Anthropic API key is configured.", "Add ANTHROPIC_API_KEY, then retry."
        )

    src = ingest(
        project, provider, settings, reporter, cancel,
        IngestOptions(opts.get("audio_track")), with_proxy=False,
    )  # fmt: skip
    project.emit("source_ready", title=src.title, warnings=src.quality.warnings)
    if not src.probe.video:
        raise ClipforgeError(
            "This source has no video.", "Character edits need a video, not an audio-only file."
        )
    src = ensure_proxy(project, src, reporter, cancel)
    vis = precompute_visual(project, src, reporter, cancel)

    t0 = time.time()
    reporter.progress("curate", 0.0, note=f'looking for "{character}"')

    def on_record(rec: UsageRecord, totals: dict) -> None:
        project.emit("cost", stage=rec.stage, model=rec.model, call_usd=round(rec.usd, 6), **totals)

    run_dir = project.path("curation", "runs")
    run_dir.mkdir(parents=True, exist_ok=True)
    meter = CostMeter(settings.max_job_cost_usd, on_record=on_record)
    llm = LLMClient(settings.anthropic_api_key.get_secret_value(), meter, run_dir, "v1")
    ref_paths = [Path(p) for p in opts.get("reference_images", [])]
    ref_images = [p.read_bytes() for p in ref_paths if p.exists()]

    scenes = cscan.scan_character(
        Path(src.proxy_path or ""), src.probe.duration, vis.get("scene_cuts", []), character,
        project.path("character", "thumbs"), llm, settings.scan_model, ref_images,
        on_progress=lambda **d: reporter.progress("curate", d.get("pct"), note=d.get("note")),
    )  # fmt: skip
    target = float(opts.get("target_duration") or 30.0)
    selected = cscan.select_scenes(scenes, target)
    reporter.stage_done("curate", round(time.time() - t0, 2), cached=False)

    if not selected:
        project.emit(
            "stage_error", stage="curate", code="no_character_match",
            message=f'No confident match for "{character}" was found.',
            action="Check the spelling, add a reference image, or try a clearer source.",
        )  # fmt: skip
        project.emit("job_done", clips=0)
        return 0

    clip = build_clip("c001", character, selected)
    save_clips(project, [clip])
    project.emit("clips_ready", count=1, run_id="character-scan", **meter.totals())
    if settings.auto_render_top:
        from clipforge.stages import _auto_render_top

        _auto_render_top(project, [clip])
    project.emit("job_done", clips=1)
    return 0
