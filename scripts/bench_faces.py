"""ADR-005: face detector benchmark (YuNet vs SCRFD-500M) on real fixture frames.

Ground truth is by construction: the interview/lecture videos contain exactly one real face in
frame for the sampled window; the screen recording and the static graphic contain none. Reports
recall (>=1 face), single-face rate, false-positive rate on no-face videos, ms/frame, plus
annotated sheets for visual verification.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from clipforge.vision.detect import make

MEDIA = Path(__file__).resolve().parent.parent / "eval/fixtures/media"
OUT = Path(__file__).resolve().parent.parent / "scratch/frames"
CASES = {  # name: (file, start_s, expected faces)
    "kende (1 face)": ("kende_internet_hall_of_fame.webm", 0, 1),
    "hannah (1 face)": ("hannah_cloke_extreme_weather.webm", 0, 1),
    "flynn (1 face)": ("flynn_right_to_research.webm", 60, 1),
    "udio screen (0)": ("udio_tutorial.webm", 0, 0),
    "podcast graphic (0)": ("direct_current_rooftop_solar.webm", 300, 0),
}


def frames(path: Path, start: float, seconds: int = 60, fps: float = 2.0) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(round(src_fps / fps), 1)
    out, i = [], 0
    while len(out) < seconds * fps:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, f = cap.retrieve()
            if ok:
                h, w = f.shape[:2]
                s = 1280 / w
                out.append(cv2.resize(f, (1280, int(h * s))) if w > 1280 else f)
        i += 1
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {name: frames(MEDIA / f, st) for name, (f, st, _) in CASES.items()}
    for det_name in ("yunet", "scrfd500m"):
        det = make(det_name)
        print(f"\n{det_name}")
        tiles = []
        for name, (_, _, want) in CASES.items():
            fs = data[name]
            t = time.time()
            res = [det.detect(f) for f in fs]
            ms = (time.time() - t) / max(len(fs), 1) * 1000
            counts = np.array([len(r) for r in res])
            if want:
                print(
                    f"  {name:22s} recall {np.mean(counts >= 1):5.0%}  exactly-one {np.mean(counts == 1):5.0%}  {ms:5.1f} ms/frame  ({len(fs)} frames)"
                )
            else:
                print(
                    f"  {name:22s} false-positive frames {np.mean(counts >= 1):5.0%}                {ms:5.1f} ms/frame  ({len(fs)} frames)"
                )
            for k in np.linspace(0, len(fs) - 1, 3).astype(int):
                img = fs[k].copy()
                for b in res[k]:
                    cv2.rectangle(
                        img, (int(b.x), int(b.y)), (int(b.x + b.w), int(b.y + b.h)), (0, 255, 0), 3
                    )
                tiles.append(cv2.resize(img, (400, 225)))
        rows = [np.hstack(tiles[i : i + 3]) for i in range(0, len(tiles), 3)]
        cv2.imwrite(str(OUT / f"faces_{det_name}.jpg"), np.vstack(rows))


if __name__ == "__main__":
    main()
