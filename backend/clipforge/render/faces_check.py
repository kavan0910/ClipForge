"""Subprocess: re-detect faces on a rendered clip. `python -m clipforge.render.faces_check <mp4> <layouts.json> <out.json>`.

face_in_crop: share of single/two-layout frames with a detected face (SCRFD, score >= 0.5).
eye_line: median eye height as a fraction of the frame, and the share inside 30-50%.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def check(path: Path, layouts: list[str], fps: float = 4.0) -> dict:
    import cv2

    from clipforge.vision.detect import make

    det = make("scrfd500m")
    cap = cv2.VideoCapture(str(path))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    step = max(round(src_fps / fps), 1)
    n = ok = i = 0
    eyes: list[float] = []
    while cap.grab():
        if i % step == 0 and i < len(layouts) and layouts[i] in ("single", "two"):
            got, frame = cap.retrieve()
            if not got:
                break
            n += 1
            faces = [b for b in det.detect(frame) if b.score >= 0.5]
            if faces:
                ok += 1
                big = max(faces, key=lambda b: b.w * b.h)
                if big.eye_y is not None:
                    eyes.append(big.eye_y / frame.shape[0])
        i += 1
    cap.release()
    a = np.asarray(eyes) if eyes else np.zeros(1)
    return {
        "frames_checked": n, "face_in_crop": round(ok / n, 4) if n else None,
        "eye_line_median": round(float(np.median(a)), 3),
        "eye_line_within_30_50": round(float(np.mean((a >= 0.30) & (a <= 0.50))), 4),
    }  # fmt: skip


if __name__ == "__main__":
    res = check(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
    Path(sys.argv[3]).write_text(json.dumps(res))
