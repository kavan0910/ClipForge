"""ADR-003: raw Whisper vs forced-aligned word onsets, judged by the independent energy-rise reference."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from clipforge.asr.align import align_words
from clipforge.captions import onsets
from clipforge.config import get_settings
from clipforge.models import Transcript


def main(names: list[str]) -> None:
    all_raw: list[float] = []
    all_al: list[float] = []
    for name in names:
        proj = get_settings().projects_dir / name
        tr = Transcript.model_validate_json((proj / "transcript" / "transcript.json").read_text())
        wav = proj / "source" / "audio_16k.wav"
        rms = onsets.rms_db_10ms(Path(wav))
        t = time.time()
        aligned = align_words(wav, tr.words, tr.sentences)
        dt = time.time() - t
        # Fixed acoustic reference: onsets found from the RAW timings' windows; both variants are scored against them.
        refs = {}
        for k in onsets.after_pause(tr.words):
            o = onsets.detect_onset(rms, tr.words[k - 1].end, tr.words[k].start)
            if o is not None:
                refs[k] = o
        raw = [refs[k] - tr.words[k].start for k in refs]
        al = [refs[k] - aligned[k].start for k in refs]
        all_raw.extend(raw)
        all_al.extend(al)
        print(
            f"{name}: aligned {len(tr.words)} words in {dt:.0f}s ({tr.duration / dt:.1f}x realtime), {len(refs)} reference onsets"
        )
        print("  raw    ", onsets.summarize(raw))
        print("  aligned", onsets.summarize(al))
    print("\nALL raw    ", onsets.summarize(all_raw))
    print("ALL aligned", onsets.summarize(all_al))


if __name__ == "__main__":
    main(sys.argv[1:] or ["fx-hannah-cloke"])
