"""ByteTrack-style multi-face tracker (numpy + scipy only, no OpenCV).

Two-stage association by IoU: confident detections first, then low-score ones against the
tracks still unmatched. Tracks survive short gaps and are only started from confident
detections. Stable IDs matter: the camera and the active-speaker vote are keyed by them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from clipforge.vision.detect import Box, iou


@dataclass
class Track:
    id: int
    boxes: list[tuple[float, Box]] = field(default_factory=list)  # (time, box)
    last_t: float = 0.0
    misses: int = 0

    @property
    def last(self) -> Box:
        return self.boxes[-1][1]


def _match(
    tracks: list[Track], dets: list[Box], min_iou: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    if not tracks or not dets:
        return [], list(range(len(tracks))), list(range(len(dets)))
    cost = np.array([[1 - iou(t.last, d) for d in dets] for t in tracks])
    rows, cols = linear_sum_assignment(cost)
    pairs = [(r, c) for r, c in zip(rows, cols, strict=True) if 1 - cost[r, c] >= min_iou]
    ur = [r for r in range(len(tracks)) if r not in {p[0] for p in pairs}]
    uc = [c for c in range(len(dets)) if c not in {p[1] for p in pairs}]
    return pairs, ur, uc


class FaceTracker:
    def __init__(
        self, high: float = 0.6, low: float = 0.3, min_iou: float = 0.2, max_gap: float = 1.5
    ) -> None:
        self.high, self.low, self.min_iou, self.max_gap = high, low, min_iou, max_gap
        self.active: list[Track] = []
        self.done: list[Track] = []
        self._next = 1

    def update(self, t: float, dets: list[Box]) -> list[tuple[int, Box]]:
        """Feed one frame's detections; returns (track_id, box) for tracks updated this frame."""
        hi = [d for d in dets if d.score >= self.high]
        lo = [d for d in dets if self.low <= d.score < self.high]
        out: list[tuple[int, Box]] = []
        pairs, unmatched_tracks, unmatched_hi = _match(self.active, hi, self.min_iou)
        for r, c in pairs:
            self.active[r].boxes.append((t, hi[c]))
            self.active[r].last_t = t
            out.append((self.active[r].id, hi[c]))
        remaining = [self.active[r] for r in unmatched_tracks]
        pairs2, _, _ = _match(remaining, lo, self.min_iou)
        for r, c in pairs2:
            remaining[r].boxes.append((t, lo[c]))
            remaining[r].last_t = t
            out.append((remaining[r].id, lo[c]))
        matched_ids = {i for i, _ in out}
        for tr in self.active:
            tr.misses = 0 if tr.id in matched_ids else tr.misses + 1
        for c in unmatched_hi:
            tr = Track(id=self._next, last_t=t)
            self._next += 1
            tr.boxes.append((t, hi[c]))
            self.active.append(tr)
            out.append((tr.id, hi[c]))
        alive: list[Track] = []
        for tr in self.active:
            (alive if t - tr.last_t <= self.max_gap else self.done).append(tr)
        self.active = alive
        return out

    def finish(self) -> list[Track]:
        return sorted([*self.done, *self.active], key=lambda t: t.id)


def synthetic_scene(
    n_people: int = 2, seconds: float = 20.0, fps: float = 8.0, size: tuple[int, int] = (1280, 720),
    seed: int = 0, occlusion: tuple[float, float] | None = None, speaking: list[tuple[float, float, int]] | None = None,
) -> tuple[list[tuple[float, list[Box]]], dict[int, list[tuple[float, float, float, float]]]]:  # fmt: skip
    """Synthetic detections for layout/camera tests: people drift slowly, detector adds noise.

    Returns (frames, truth) where truth[person] lists (t, cx, cy, size). `occlusion` drops
    person 0 for a time range (tracks must survive it). Person i sits at x = (i+1)/(n+1).
    """
    rng = np.random.default_rng(seed)
    w, h = size
    frames: list[tuple[float, list[Box]]] = []
    truth: dict[int, list[tuple[float, float, float, float]]] = {i: [] for i in range(n_people)}
    for k in range(int(seconds * fps)):
        t = k / fps
        dets: list[Box] = []
        for i in range(n_people):
            cx = w * (i + 1) / (n_people + 1) + 12 * np.sin(t * 0.7 + i)
            cy = h * 0.38 + 6 * np.cos(t * 0.5 + i)
            fs = h * 0.22
            truth[i].append((t, float(cx), float(cy), float(fs)))
            if occlusion and i == 0 and occlusion[0] <= t <= occlusion[1]:
                continue
            j = rng.normal(0, 1.5, 4)
            dets.append(
                Box(
                    cx - fs / 2 + j[0],
                    cy - fs / 2 + j[1],
                    fs + j[2],
                    fs + j[3],
                    float(rng.uniform(0.7, 0.95)),
                )
            )
        if rng.random() < 0.03:  # occasional false positive
            dets.append(
                Box(float(rng.uniform(0, w - 40)), float(rng.uniform(0, h - 40)), 40, 40, 0.65)
            )
        frames.append((t, dets))
    return frames, truth
