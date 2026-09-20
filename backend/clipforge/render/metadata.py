"""metadata.json for a clip: everything needed to publish it and to reproduce how it was made."""

from __future__ import annotations

import json
from pathlib import Path

from clipforge.curate.schema import Clip
from clipforge.edl import EDL
from clipforge.store import Project


def _run_info(project: Project, clip_id: str) -> dict:
    runs = (
        sorted(project.path("curation", "runs").glob("run-*/run.json"))
        if project.path("curation").exists()
        else []
    )
    for f in reversed(runs):
        info = json.loads(f.read_text())
        if clip_id in info.get("clip_ids", []):
            return info
    return {}


def write_metadata(project: Project, clip: Clip, edl: EDL, d: Path, extra: dict) -> Path:
    run = _run_info(project, clip.id)
    meta = {
        "title": clip.title, "hook": clip.hook, "description": clip.description, "hashtags": clip.hashtags,
        "summary": clip.summary, "why_it_works": clip.why_it_works, "risk_flags": clip.risk_flags,
        "scores": clip.scores.model_dump(), "overall": clip.overall, "clip_score_heuristic": clip.rank_score,
        "hook_check": clip.hook_check.model_dump(),
        "source": {"start": clip.start, "end": clip.end, "start_sentence": clip.start_sentence, "end_sentence": clip.end_sentence},
        "duration_seconds": round(edl.duration, 3),
        "edl": edl.model_dump(),
        "models": run.get("models", {}), "prompt_version": run.get("prompt_version"),
        "curation_cost_usd": (run.get("usage") or {}).get("usd"), "curation_run": run.get("run_id"),
        **extra,
    }  # fmt: skip
    path = d / "metadata.json"
    path.write_text(json.dumps(meta, indent=1))
    return path
