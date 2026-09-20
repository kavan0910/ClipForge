"""Punch-ins for the fit layout: small pushes on emphasised words plus a slow drift inside each take.

A picture that never moves for 40 seconds reads as unedited. These moves are deliberately small (peak +8%) and,
in the fit layout, cost no sharpness at all: the card shows the whole frame shrunk to half size, so cropping in a
little still leaves the picture reduced, never enlarged. The push is asymmetric (quick in, short hold, slow out),
which is how an editor does it by hand.
"""

from __future__ import annotations

import numpy as np

from clipforge.reframe.camera import Rect

PEAK = 1.08
RISE, HOLD, FALL = 0.28, 0.45, 0.9  # seconds
MIN_GAP = 2.5  # never push twice within this
DRIFT = 0.035  # slow push across each uncut stretch
MIN_DRIFT_TAKE = 4.0


def _smooth(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def zoom_curve(
    n: int,
    fps: float,
    beats: list[float],
    take_bounds: list[tuple[float, float]] | None = None,
    peak: float = PEAK,
) -> np.ndarray:
    """Zoom factor per output frame (1.0 = none)."""
    t = (np.arange(n) + 0.5) / fps
    z = np.ones(n)
    last = -1e9
    for b in sorted(beats):
        if b - last < MIN_GAP:
            continue
        last = b
        rise = _smooth((t - b) / RISE)
        fall = 1.0 - _smooth((t - (b + RISE + HOLD)) / FALL)
        env = np.where(t < b, 0.0, np.minimum(rise, fall))
        z = np.maximum(z, 1.0 + (peak - 1.0) * env)
    for a, b in take_bounds or []:
        if b - a >= MIN_DRIFT_TAKE:
            prog = _smooth((t - a) / (b - a))
            inside = (t >= a) & (t < b)
            z = np.where(inside, z * (1.0 + DRIFT * prog), z)
    return z


def apply_fit_punch(
    frames: list, curve: np.ndarray, src_w: float, src_h: float, focus_x: float = 0.5
) -> int:
    """Give every fit frame a cropped rectangle for its zoom. Returns how many frames moved."""
    moved = 0
    for f, spec in enumerate(frames):
        if spec.layout != "fit_blur" or f >= len(curve):
            continue
        z = float(curve[f])
        w, h = src_w / z, src_h / z
        x = float(np.clip(focus_x * src_w - w / 2, 0, src_w - w))
        y = float(
            np.clip((src_h - h) * 0.38, 0, src_h - h)
        )  # keep a little more headroom than floor
        spec.rects = [Rect(x, y, w, h)]
        moved += z > 1.001
    return moved
