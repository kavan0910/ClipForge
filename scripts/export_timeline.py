"""Build a caption timeline + template JSON for a rendered clip (used by the Remotion renderer/benchmark)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from clipforge.captions.spec import load_template
from clipforge.captions.timeline import build_timeline
from clipforge.config import get_settings
from clipforge.curate.run import load_clips
from clipforge.edl import EDL
from clipforge.stages import load_transcript
from clipforge.store import Project


def main(
    project_id: str, clip_id: str, template_id: str, out_dir: str, max_seconds: float = 0
) -> None:
    p = Project.open(get_settings().projects_dir, project_id)
    clip = next(c for c in load_clips(p) if c.id == clip_id)
    edl = EDL.model_validate_json((p.path("clips", clip_id, "edl.json")).read_text())
    tr = load_transcript(p)
    t = load_template(template_id)
    words = edl.remap_words(tr.words)
    if max_seconds:
        words = [w for w in words if w.out_end <= max_seconds]
    dur = max_seconds or edl.duration
    tl = build_timeline(words, t, dur, set(clip.emphasis_word_indices), hook=clip.hook)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "timeline.json").write_text(tl.model_dump_json())
    (out / "template.json").write_text(t.model_dump_json())
    print(json.dumps({"chunks": len(tl.chunks), "duration": dur, "hook": bool(tl.hook)}))


if __name__ == "__main__":
    main(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        sys.argv[4],
        float(sys.argv[5]) if len(sys.argv) > 5 else 0,
    )
