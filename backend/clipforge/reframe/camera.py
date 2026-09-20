"""Virtual camera: smooth crop paths with a dead zone, velocity/acceleration limits and
motivated moves (cut when far, pan when near).

Offline, so smoothing is symmetric (uses look-ahead). All paths live on a uniform grid at
PATH_FPS and are sampled per output frame. Jerk is defined and measured here:

    jerk = third derivative of the crop-centre x, in frame-widths per second cubed, computed
    on the final per-frame path with cut frames excluded. Acceptance: p99 <= 60 and max <= 100 (the smoothstep pan endpoints add a transient of up to ~2x).
    The limit comes from the geometry of the pans the brief allows: a smoothstep pan of distance
    D (frame widths) over T seconds has constant |jerk| = 12*D/T^3, so a 0.25-frame-width pan
    over 0.40 s is ~47. Pan time grows with distance (0.25 to 0.40 s). Jitter fails easily: a
    2 px wobble at 30 fps on a 1080 px frame is thousands.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from scipy.ndimage import gaussian_filter1d

PATH_FPS = 60.0
DEAD_ZONE = 0.125  # each side, as a fraction of crop width: subject stays in the central 25%
EYE_LINE = 0.40  # eyes at 40% from the top of the crop
CUT_DISTANCE = 0.35  # move farther than this fraction of frame width -> hard cut
PAN_MIN_SECONDS, PAN_MAX_SECONDS = 0.25, 0.40
JERK_P99_LIMIT = 60.0  # frame-widths/s^3; see module docstring
JERK_MAX_LIMIT = 100.0
V_MAX = 0.60  # crop widths per second
A_MAX = 2.5  # crop widths per second squared
MAX_ZOOM = 1.35
VERTICAL_DEAD_ZONE = 0.05  # tighter than horizontal: the eye line must stay inside 30-50%
LEAD_ROOM = 0.06  # bias toward the frame centre, at most 10% of crop width


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float


def crop_size(src_w: int, src_h: int, aspect: float, zoom: float) -> tuple[float, float]:
    """Largest crop of the given aspect (w/h) that fits, divided by zoom."""
    ch = float(src_h)
    cw = ch * aspect
    if cw > src_w:
        cw = float(src_w)
        ch = cw / aspect
    return cw / zoom, ch / zoom


def fill_gaps(x: np.ndarray, fallback: float) -> np.ndarray:
    """Interpolate NaNs linearly, hold the ends; all-NaN input becomes `fallback`."""
    x = np.asarray(x, dtype=float).copy()
    ok = ~np.isnan(x)
    if not ok.any():
        return np.full_like(x, fallback)
    idx = np.arange(len(x))
    x[~ok] = np.interp(idx[~ok], idx[ok], x[ok])
    return x


def resample(t_src: np.ndarray, x: np.ndarray, t_dst: np.ndarray) -> np.ndarray:
    return np.interp(t_dst, t_src, x)


def follow(
    target: np.ndarray,
    dt: float,
    crop_size_px: float,
    sigma_s: float = 0.35,
    dead_zone: float = DEAD_ZONE,
) -> np.ndarray:
    """Dead-zone follower with velocity and acceleration limits.

    `target` is the subject position (pixels) on a uniform grid with step `dt`. The camera stays
    still while the subject is inside the central 25% of the crop, then moves just enough to keep
    them inside, never faster than V_MAX or accelerating harder than A_MAX (crop widths).
    """
    tgt = gaussian_filter1d(target, sigma_s / dt, mode="nearest")
    dz = dead_zone * crop_size_px
    cam = np.empty_like(tgt)
    cam[0] = tgt[0]
    for k in range(1, len(tgt)):
        err = tgt[k] - cam[k - 1]
        cam[k] = cam[k - 1] + (err - np.sign(err) * dz if abs(err) > dz else 0.0)
    vmax, amax = V_MAX * crop_size_px, A_MAX * crop_size_px
    out = np.empty_like(cam)
    out[0] = cam[0]
    v = 0.0
    for k in range(1, len(cam)):
        want_v = np.clip((cam[k] - out[k - 1]) / dt, -vmax, vmax)
        v = float(np.clip(want_v, v - amax * dt, v + amax * dt))
        out[k] = out[k - 1] + v * dt
    return gaussian_filter1d(out, 0.18 / dt, mode="nearest")


def smoothstep(u: np.ndarray) -> np.ndarray:
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3 - 2 * u)


def stitch_focus(
    grid: np.ndarray, intervals: list[tuple[float, float, int]], paths: dict[int, np.ndarray], frame_w: float
) -> tuple[np.ndarray, list[int]]:  # fmt: skip
    """Combine per-track paths by focus interval: hard cut if the move is large, else a pan.

    Returns (path, cut_indices) where cut_indices are grid indices of hard cuts.
    """
    out = paths[intervals[0][2]].copy()
    cuts: list[int] = []
    for (_, _, prev_id), (t0, _, new_id) in pairwise(intervals):
        k = int(np.searchsorted(grid, t0))
        k = min(k, len(grid) - 1)
        cur = out[k] if k < len(out) else out[-1]
        dist = abs(paths[new_id][k] - cur)
        if dist > CUT_DISTANCE * frame_w:
            out[k:] = paths[new_id][k:]
            cuts.append(k)
        else:
            secs = float(
                np.clip(PAN_MIN_SECONDS + dist / frame_w, PAN_MIN_SECONDS, PAN_MAX_SECONDS)
            )
            w = smoothstep(np.arange(len(grid) - k) / max(secs * PATH_FPS, 1.0))
            out[k:] = (1 - w) * cur_path_after(out, paths[prev_id], k) + w * paths[new_id][k:]
    return out, cuts


def cur_path_after(out: np.ndarray, prev_path: np.ndarray, k: int) -> np.ndarray:
    """The previous subject's path continues through the pan window (not the stitched result)."""
    return prev_path[k:]


def zoom_for_face(face_h: float, eye_y: float, frame_h: float) -> float:
    """Zoom so the face is a comfortable size and the eye line can reach ~40% of the crop."""
    z_face = 0.20 * frame_h / max(face_h, 1.0)
    z_eye = 0.36 * frame_h / max(eye_y, 1.0)  # face near the top: zoom in to lower the eye line
    return float(np.clip(max(z_face, z_eye, 1.0), 1.0, MAX_ZOOM))


def path_jerk(x_frac: np.ndarray, fps: float, cut_frames: list[int]) -> dict[str, float]:
    """Jerk statistics for a per-frame crop-centre path (units: frame widths / s^3)."""
    d3 = np.diff(x_frac, n=3) * fps**3
    mask = np.ones(len(d3), dtype=bool)
    for c in cut_frames:
        mask[max(c - 3, 0) : c + 1] = False  # third differences touching a cut are undefined
    d = np.abs(d3[mask]) if mask.any() else np.zeros(1)
    return {"p99": float(np.percentile(d, 99)), "max": float(d.max()), "mean": float(d.mean())}


def lead_room_bias(face_cx: float, frame_w: float, crop_w: float) -> float:
    """Shift the crop centre toward the frame centre (where people usually look), up to 6% of crop."""
    off = (frame_w / 2 - face_cx) / max(frame_w / 2, 1.0)
    return float(np.clip(off, -1, 1) * LEAD_ROOM * crop_w)
