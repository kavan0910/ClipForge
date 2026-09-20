"""Visual signals from the 720p proxy: shot changes (PySceneDetect) and motion energy."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from clipforge.procs import Cancelled, CancelToken


def shot_changes(proxy: Path) -> list[float]:
    """Shot-boundary times in seconds (adaptive content detector)."""
    from scenedetect import AdaptiveDetector, detect

    scenes = detect(str(proxy), AdaptiveDetector(), show_progress=False)
    return [round(start.get_seconds(), 3) for start, _ in scenes[1:]]


def motion_energy(
    proxy: Path, duration: float, cancel: CancelToken, sample_fps: float = 2.0
) -> list[float]:
    """Mean absolute frame difference (grayscale, 160 px wide) per second, sampled at 2 fps."""
    import cv2

    cap = cv2.VideoCapture(str(proxy))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(round(fps / sample_fps), 1)
    n = int(np.ceil(duration))
    total, count = np.zeros(n), np.zeros(n)
    prev = None
    i = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if i % step == 0:
                if cancel.cancelled:
                    raise Cancelled
                ok, frame = cap.retrieve()
                if not ok:
                    break
                g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(
                    np.float32
                )
                if prev is not None:
                    sec = min(int(i / fps), n - 1)
                    total[sec] += float(np.abs(g - prev).mean())
                    count[sec] += 1
                prev = g
            i += 1
    finally:
        cap.release()
    return np.divide(total, np.maximum(count, 1)).tolist()


def cut_density(cuts: list[float], duration: float, window: int = 10) -> list[float]:
    """Shot changes per second, smoothed over `window` seconds."""
    n = int(np.ceil(duration))
    a = np.zeros(n)
    for c in cuts:
        if 0 <= int(c) < n:
            a[int(c)] += 1
    return np.convolve(a, np.ones(window) / window, mode="same").tolist()


def main(argv: list[str]) -> int:
    """Subprocess entry: `python -m clipforge.signals.visual <proxy> <duration> <out.json>`.

    OpenCV and PyAV (pulled in by faster-whisper) each bundle libavdevice; loading both in one
    process risks crashes, so all OpenCV work runs in its own interpreter.
    """
    import json
    import os

    proxy, duration, out = Path(argv[0]), float(argv[1]), Path(argv[2])
    from clipforge.procs import CancelToken, cancel_on_signals

    token = CancelToken()
    cancel_on_signals(token)
    cuts = shot_changes(proxy)
    motion = motion_energy(proxy, duration, token)
    tmp = out.with_suffix(".partial")
    tmp.write_text(json.dumps({"scene_cuts": cuts, "motion": motion}))
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
