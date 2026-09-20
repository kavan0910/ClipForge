"""Face analysis subprocess: `python -m clipforge.vision.analyze <proxy> <out.json> <start> <end> [fps]`.

Runs detection at ~8 fps on the 720p proxy, tracks faces with stable IDs and computes a
mouth-motion signal per track (frame difference of the lower face region). Kept in its own
process because OpenCV and PyAV must not share an interpreter.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

from clipforge.procs import CancelToken, cancel_on_signals
from clipforge.vision.detect import Box, make
from clipforge.vision.track import FaceTracker

ANALYSIS_VERSION = 2


def mouth_patch(gray: np.ndarray, b: Box) -> np.ndarray | None:
    import cv2

    h, w = gray.shape
    x0, x1 = int(b.x + b.w * 0.25), int(b.x + b.w * 0.75)
    y0, y1 = int(b.y + b.h * 0.60), int(b.y + b.h * 0.98)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return cv2.resize(gray[y0:y1, x0:x1], (24, 12)).astype(np.float32)


def analyze(proxy: Path, start: float, end: float, fps: float, token: CancelToken) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(proxy))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(int(start * src_fps) - 1, 0))
    det, tracker = make("scrfd500m"), FaceTracker()
    step = max(round(src_fps / fps), 1)
    frames: list[dict] = []
    prev_patch: dict[int, np.ndarray] = {}
    i = int(max(int(start * src_fps) - 1, 0))
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            t = i / src_fps
            i += 1
            if t < start:
                continue
            if t > end:
                break
            if (i - 1) % step:
                continue
            if token.cancelled:
                break
            ok, frame = cap.retrieve()
            if not ok:
                break
            dets = det.detect(frame)
            updated = tracker.update(t, [d for d in dets if d.score >= 0.3])
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = []
            for tid, b in updated:
                patch = mouth_patch(gray, b)
                motion = 0.0
                if patch is not None:
                    if tid in prev_patch:
                        motion = float(np.abs(patch - prev_patch[tid]).mean() / 255.0)
                    prev_patch[tid] = patch
                faces.append({"id": tid, "x": b.x, "y": b.y, "w": b.w, "h": b.h, "score": round(b.score, 3),
                              "eye_y": b.eye_y, "mouth": round(motion, 5)})  # fmt: skip
            frames.append({"t": round(t, 3), "faces": faces})
    finally:
        cap.release()
    return {"version": ANALYSIS_VERSION, "fps": fps, "width": width, "height": height, "start": start,
            "end": end, "frames": frames}  # fmt: skip


def main(argv: list[str]) -> int:
    proxy, out = Path(argv[0]), Path(argv[1])
    start, end = float(argv[2]), float(argv[3])
    fps = float(argv[4]) if len(argv) > 4 else 8.0
    token = CancelToken()
    cancel_on_signals(token)
    result = analyze(proxy, start, end, fps, token)
    tmp = out.with_suffix(".partial")
    tmp.write_text(json.dumps(result))
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
