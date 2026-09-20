"""Write curation/clips.json for a project from the hand-labelled good moments.

Only for testing Phase 3 (cut/reframe/audio) while the LLM is unavailable. Clips are marked
'[oracle]' in the title and carry placeholder scores; they go through the same deterministic
snapping as LLM proposals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from clipforge.config import get_settings
from clipforge.curate.postprocess import Params, build_clip, resolve
from clipforge.curate.run import save_clips
from clipforge.curate.schema import ClipProposal, Scores
from clipforge.pipeline import rms_db_frames
from clipforge.stages import load_source, load_transcript
from clipforge.store import Project

EVAL = Path(__file__).resolve().parent.parent / "eval"


def main(project_id: str, video_id: str) -> None:
    project = Project.open(get_settings().projects_dir, project_id)
    src, tr = load_source(project), load_transcript(project)
    labels = json.loads((EVAL / "labels" / f"{video_id}.json").read_text())
    rms = rms_db_frames(Path(src.audio_path or ""))
    clips = []
    for k, g in enumerate(labels["good"]):
        a = min(tr.sentences, key=lambda s: abs(s.start - g["start"]))
        b = min(tr.sentences, key=lambda s: abs(s.end - g["end"]))
        p = ClipProposal(
            start_sentence=a.id, end_sentence=b.id, title=f"[oracle] {g['note'][:44]}", hook="Placeholder hook",
            hook_evidence_start=a.id, hook_evidence_end=b.id, summary=g["note"], why_it_works="hand-labelled",
            scores=Scores(hook=70, self_contained=70, single_idea=70, payoff=70, emotion_novelty_utility=70, shareability=70),
            overall=70, emphasis=[], description=g["note"], hashtags=["oracle"], risk_flags=[],
        )  # fmt: skip
        r, why = resolve(p, tr.sentences, tr.words, rms, src.probe.duration, Params())
        if r:
            clips.append(build_clip(f"c{k + 1:03d}", r, tr.sentences, tr.words, 0.5, 0.5))
        else:
            print("skipped", g["note"], why)
    save_clips(project, clips)
    print(f"{len(clips)} oracle clips written for {project_id}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
