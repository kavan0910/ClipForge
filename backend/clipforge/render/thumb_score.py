"""Subprocess: score candidate thumbnail frames. `python -m clipforge.render.thumb_score <out.json> <png>...`

Score = sharpness (variance of Laplacian, log-scaled) + face (SCRFD score, size, upper-frame placement)
+ open eyes (Haar eye cascade finds two eyes inside the face box). Blurry, faceless or eyes-closed frames lose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def eye_openness(gray: np.ndarray, b) -> float:
    """1.0 when both eyes look open. Open eyes carry far more local contrast (iris, lashes, sclera)
    than a closed lid, so compare each eye patch's std-dev to a cheek patch from the same face."""
    if not b.kps:
        return 0.0
    h, w = gray.shape
    r = max(int(0.16 * b.w), 6)

    def patch(cx: float, cy: float) -> np.ndarray:
        x, y = int(cx), int(cy)
        return gray[max(y - r // 2, 0) : min(y + r // 2, h), max(x - r, 0) : min(x + r, w)]

    eyes = [patch(b.kps[0], b.kps[1]), patch(b.kps[2], b.kps[3])]
    cheek = patch(b.kps[4], b.kps[5] + 0.30 * b.h)  # below the nose: plain skin
    if cheek.size == 0 or any(e.size == 0 for e in eyes):
        return 0.0
    base = max(float(cheek.std()), 2.0)
    return 1.0 if all(float(e.std()) / base >= 2.0 for e in eyes) else 0.0


def score(path: Path, det) -> dict:
    import cv2

    img = cv2.imread(str(path))
    assert img is not None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = float(np.log1p(cv2.Laplacian(gray, cv2.CV_64F).var()))
    faces = [b for b in det.detect(img) if b.score >= 0.5]
    face = eyes_open = 0.0
    box = None
    if faces:
        b = max(faces, key=lambda b: b.w * b.h)
        h, w = gray.shape
        size = min(b.h / (h * 0.30), 1.0)  # a face about 30% of the frame height is ideal
        centred = 1.0 - min(abs(b.cx - w / 2) / (w / 2), 1.0) * 0.5
        face = 0.6 * float(b.score) + 0.3 * size + 0.1 * centred
        eyes_open = eye_openness(gray, b)
        box = [round(b.x), round(b.y), round(b.w), round(b.h)]
    return {"path": str(path), "sharpness": round(sharp, 3), "face": round(face, 3), "eyes_open": eyes_open, "face_box": box,
            "total": round(sharp / 8.0 + 1.5 * face + 0.8 * eyes_open, 4)}  # fmt: skip


def main(argv: list[str]) -> int:
    from clipforge.vision.detect import make

    det = make("scrfd500m")
    rows = [score(Path(p), det) for p in argv[1:]]
    Path(argv[0]).write_text(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
