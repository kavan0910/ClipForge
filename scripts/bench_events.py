"""ADR-007: laughter/applause event tagging, PANNs CNN14 vs YAMNet (tflite), on labelled clips.

Positives: applause.ogg (applause), laughter_crowd.wav (crowd laughing). Negatives: 3 minutes of
interview speech (no laughter) and 3 minutes of orchestral/animated music. Metric: fraction of
1-second windows whose max class probability exceeds 0.3.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "eval/fixtures/media"
YAM = ROOT / "scratch/yamnet"


def load(path: Path, seconds: float | None = None, rate: int = 16000) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        argv = ["ffmpeg", "-v", "error", "-y", "-i", str(path)]
        argv += ["-t", str(seconds)] if seconds else []
        subprocess.run([*argv, "-vn", "-ac", "1", "-ar", str(rate), str(wav)], check=True)
        with wave.open(str(wav)) as w:
            return (
                np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
                / 32768
            )


class Panns:
    name = "PANNs CNN14 (torch)"

    def __init__(self) -> None:
        from panns_inference import SoundEventDetection, labels

        self.sed = SoundEventDetection(checkpoint_path=None, device="cpu")
        self.idx = {n: i for i, n in enumerate(labels)}

    rate = 32000  # PANNs models are trained on 32 kHz audio

    def per_second(self, x: np.ndarray, cls: str) -> np.ndarray:
        out = []
        for s in range(0, len(x) - self.rate + 1, self.rate * 10):
            chunk = x[s : s + self.rate * 10]
            if len(chunk) < self.rate:
                break
            fr = self.sed.inference(chunk[None])[0][:, self.idx[cls]]  # 100 frames per second
            out += [
                float(fr[i * 100 : (i + 1) * 100].max()) for i in range(len(chunk) // self.rate)
            ]
        return np.array(out)


class Yamnet:
    name = "YAMNet (tflite)"
    rate = 16000

    def __init__(self) -> None:
        from ai_edge_litert.interpreter import Interpreter

        self.it = Interpreter(model_path=str(YAM / "yamnet.tflite"))
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]
        self.out = self.it.get_output_details()[0]
        rows = list(csv.reader((YAM / "classes.csv").open()))[1:]
        self.idx = {r[2]: int(r[0]) for r in rows}

    def per_second(self, x: np.ndarray, cls: str) -> np.ndarray:
        n = self.inp["shape"][0]  # 15600 samples = 0.975 s
        probs = []
        for s in range(0, len(x) - n + 1, n):
            self.it.set_tensor(self.inp["index"], x[s : s + n])
            self.it.invoke()
            probs.append(float(self.it.get_tensor(self.out["index"])[0][self.idx[cls]]))
        arr = np.array(probs)
        return arr


def main() -> None:
    clips = {
        "applause (pos)": (MEDIA / "applause.ogg", None, "Applause"),
        "crowd laughter (pos)": (MEDIA / "laughter_crowd.wav", None, "Laughter"),
        "interview speech (neg)": (MEDIA / "kende_internet_hall_of_fame.webm", 180, "Laughter"),
        "interview speech (neg, applause)": (
            MEDIA / "kende_internet_hall_of_fame.webm",
            180,
            "Applause",
        ),
    }
    for model in (Panns(), Yamnet()):
        print(f"\n{model.name}")
        for label, (path, secs, cls) in clips.items():
            x = load(path, secs)
            t = time.time()
            p = model.per_second(x, cls)
            dt = time.time() - t
            print(
                f"  {label:34s} {cls:9s} windows>0.3: {np.mean(p > 0.3):5.0%}  max {p.max():.2f}  "
                f"({len(x) / 16000 / max(dt, 1e-6):.0f}x realtime)"
            )


if __name__ == "__main__":
    sys.exit(main())
