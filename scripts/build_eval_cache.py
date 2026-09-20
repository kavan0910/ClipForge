"""Freeze a processed project into eval/cache/<id>/ so evals run without media or ASR.

Stores source.json (paths blanked), transcript.json, signals.json and rms_db.json (100 ms RMS
frames used to snap cuts to silence). Signals are computed here if the project lacks them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from clipforge.config import get_settings
from clipforge.pipeline import Reporter, load_source, rms_db_frames
from clipforge.procs import CancelToken
from clipforge.stages import load_transcript, signals_for
from clipforge.store import Project

ROOT = Path(__file__).resolve().parent.parent / "eval" / "cache"


def main(project_id: str, video_id: str) -> None:
    settings = get_settings()
    project = Project.open(settings.projects_dir, project_id)
    src, tr = load_source(project), load_transcript(project)
    sig = signals_for(project, src, tr, Reporter(project), CancelToken())
    out = ROOT / video_id
    out.mkdir(parents=True, exist_ok=True)
    frozen = src.model_copy(update={"master_path": "", "proxy_path": None, "audio_path": None})
    (out / "source.json").write_text(frozen.model_dump_json(indent=1))
    (out / "transcript.json").write_text(tr.model_dump_json())
    (out / "signals.json").write_text(sig.model_dump_json())
    rms = [round(x, 1) for x in rms_db_frames(Path(src.audio_path or ""))]
    (out / "rms_db.json").write_text(json.dumps(rms))
    print(
        f"{video_id}: {len(tr.words)} words, {len(tr.sentences)} sentences, {len(rms)} rms frames"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
