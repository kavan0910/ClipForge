"""ADR-001 benchmark: mlx-whisper models on a fixture excerpt.

Reports wall time, real-time factor and peak RSS per model. There is no human
reference transcript, so accuracy is reported as word error rate of each model
against the largest model (agreement, not ground truth).
"""

from __future__ import annotations

import json
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "eval/fixtures/media/kende_internet_hall_of_fame.webm"
MODELS = [
    "mlx-community/whisper-large-v3-mlx",
    "mlx-community/whisper-large-v3-turbo",
]


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[-1] / max(len(ref), 1)


def child(model: str, wav: str) -> None:
    import mlx_whisper

    t0 = time.time()
    out = mlx_whisper.transcribe(
        wav,
        path_or_hf_repo=model,
        word_timestamps=True,
        condition_on_previous_text=False,
        verbose=None,
    )
    wall = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # bytes on macOS -> MiB
    import mlx.core as mx

    gpu = mx.get_peak_memory() / 2**30  # GiB of Metal memory (weights + activations)
    segments: Any = out["segments"]
    ws = [w for s in segments for w in s.get("words", [])]
    print(
        json.dumps(
            {
                "model": model,
                "wall_s": wall,
                "peak_rss_mib": rss,
                "peak_metal_gib": gpu,
                "text": out["text"],
                "n_words": len(ws),
                "words": ws,
            }
        )
    )


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    with tempfile.TemporaryDirectory() as tmp:
        wav = str(Path(tmp) / "a.wav")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(SRC),
                "-t",
                str(seconds),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                wav,
            ],
            check=True,
        )
        runs = []
        for m in MODELS:
            p = subprocess.run(
                [sys.executable, __file__, "--child", m, wav],
                capture_output=True,
                text=True,
                check=False,
            )
            if p.returncode:
                print(p.stderr[-800:])
                continue
            r = json.loads(p.stdout.strip().splitlines()[-1])
            r["rtf_x_realtime"] = seconds / r["wall_s"]
            runs.append(r)
            print(
                f"{m}: {r['wall_s']:.1f}s, {r['rtf_x_realtime']:.1f}x realtime, "
                f"RSS {r['peak_rss_mib']:.0f} MiB, Metal {r['peak_metal_gib']:.2f} GiB, "
                f"{r['n_words']} words",
                flush=True,
            )
    ref = words(runs[0]["text"]) if runs else []
    for r in runs:
        r["wer_vs_largest"] = wer(ref, words(r["text"]))
        r.pop("words")
    (ROOT / "eval/results/adr001_asr.json").write_text(json.dumps(runs, indent=2))
    for r in runs:
        print(r["model"], "WER vs largest:", round(r["wer_vs_largest"], 4))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    else:
        main()
