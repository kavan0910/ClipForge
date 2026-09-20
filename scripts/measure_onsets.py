"""Measure caption word-onset accuracy on every processed project (raw ASR timing vs refined)."""

from __future__ import annotations

from pathlib import Path

from clipforge.captions import onsets
from clipforge.config import get_settings
from clipforge.models import Transcript


def main() -> None:
    allraw, allref = [], []
    for proj in sorted(get_settings().projects_dir.iterdir()):
        tf, wav = proj / "transcript" / "transcript.json", proj / "source" / "audio_16k.wav"
        if not (tf.exists() and wav.exists()):
            continue
        tr = Transcript.model_validate_json(tf.read_text())
        rms = onsets.rms_db_10ms(Path(wav))
        raw = onsets.onset_errors(tr.words, rms)
        # Refined words are checked against the same acoustic reference.
        ref = onsets.onset_errors(onsets.refine(tr.words, rms), rms)
        allraw += raw
        allref += ref
        print(f"{proj.name:18s} raw {onsets.summarize(raw)}")
        print(f"{'':18s} refined {onsets.summarize(ref)}")
    print("\nALL raw    ", onsets.summarize(allraw))
    print("ALL refined", onsets.summarize(allref))


if __name__ == "__main__":
    main()
