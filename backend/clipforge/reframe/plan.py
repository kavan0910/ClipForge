"""Layout planning: which layout is on screen when, and which face the camera follows.

Pure functions over the face analysis (tracks over time), diarization turns and shot cuts.
Rules from the brief: hysteresis (no layout change faster than 1.2 s), wait 0.4 s after speech
onset before switching subject, stacked split when speakers alternate faster than ~2 s, a
two-shot when both people fit one crop, screen+facecam when a small static face is present,
and a blurred fit-to-width fallback when there are no usable faces.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

import numpy as np
from scipy.optimize import linear_sum_assignment

Layout = Literal["single", "two", "stacked", "screen_cam", "fit_blur"]
MIN_LAYOUT_DWELL = 1.2
SWITCH_DELAY = 0.4
MIN_FOCUS_DWELL = 1.2


@dataclass
class TrackSeries:
    """One face track on the analysis time grid (NaN where absent)."""

    id: int
    cx: np.ndarray
    cy: np.ndarray
    w: np.ndarray
    h: np.ndarray
    eye_y: np.ndarray
    mouth: np.ndarray

    @property
    def presence(self) -> np.ndarray:
        return ~np.isnan(self.cx)


@dataclass
class Scene:
    """Analysis resampled to a regular grid."""

    t: np.ndarray  # seconds (source time)
    width: int
    height: int
    tracks: dict[int, TrackSeries]


@dataclass
class Turn:
    start: float
    end: float
    speaker: str


@dataclass
class LayoutSegment:
    t0: float
    t1: float
    layout: Layout
    focus: list[tuple[float, float, int]] = field(default_factory=list)  # (t0, t1, track id)
    cam_track: int | None = None  # for screen_cam
    tracks: list[int] = field(default_factory=list)  # for two / stacked
    override: bool = False


def scene_from_analysis(analysis: dict) -> Scene:
    """Resample per-frame detections onto the analysis grid, one series per track id."""
    fps = analysis["fps"]
    t0, t1 = analysis["start"], analysis["end"]
    grid = np.arange(t0, t1, 1.0 / fps)
    idx = {round(t, 3): k for k, t in enumerate(grid)}
    ids = sorted({f["id"] for fr in analysis["frames"] for f in fr["faces"]})
    arrs = {
        i: {k: np.full(len(grid), np.nan) for k in ("cx", "cy", "w", "h", "eye_y", "mouth")}
        for i in ids
    }
    for fr in analysis["frames"]:
        k = (
            min(range(len(grid)), key=lambda j: abs(grid[j] - fr["t"]))
            if fr["t"] not in idx
            else idx[fr["t"]]
        )
        for f in fr["faces"]:
            a = arrs[f["id"]]
            a["cx"][k], a["cy"][k] = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
            a["w"][k], a["h"][k] = f["w"], f["h"]
            a["eye_y"][k] = f["eye_y"] if f.get("eye_y") is not None else f["y"] + 0.4 * f["h"]
            a["mouth"][k] = f["mouth"]
    tracks = {i: TrackSeries(i, **a) for i, a in arrs.items()}
    return Scene(grid, analysis["width"], analysis["height"], tracks)


def significant_tracks(scene: Scene, min_presence: float = 0.25) -> list[TrackSeries]:
    """Tracks present in enough of the window to matter (drops flickers and false positives)."""
    n = len(scene.t)
    return [t for t in scene.tracks.values() if n and t.presence.sum() / n >= min_presence]


def map_speakers_to_tracks(scene: Scene, turns: list[Turn]) -> dict[str, tuple[int, float]]:
    """Active-speaker mapping by mouth motion: speaker -> (track id, confidence).

    For each (speaker, track): mean mouth motion while that speaker talks minus while others
    talk. One-to-one assignment (Hungarian); the vote is over the whole window.
    """
    tracks = significant_tracks(scene)
    speakers = sorted({t.speaker for t in turns})
    if not tracks or not speakers:
        return {}
    score = np.zeros((len(speakers), len(tracks)))
    for si, s in enumerate(speakers):
        mine = np.zeros(len(scene.t), dtype=bool)
        for t in turns:
            if t.speaker == s:
                mine |= (scene.t >= t.start) & (scene.t < t.end)
        others = ~mine
        for ti, tr in enumerate(tracks):
            ok = tr.presence
            a, b = tr.mouth[mine & ok], tr.mouth[others & ok]
            if len(a) >= 3 and len(b) >= 3:
                score[si, ti] = float(a.mean() - b.mean())
            elif len(a) >= 3:
                score[si, ti] = float(a.mean())
    rows, cols = linear_sum_assignment(-score)
    span = max(score.max() - score.min(), 1e-6)
    return {
        speakers[r]: (tracks[c].id, float((score[r, c] - score.min()) / span))
        for r, c in zip(rows, cols, strict=True)
    }


def dominant_track(scene: Scene) -> int | None:
    best, best_v = None, -1.0
    for t in significant_tracks(scene, 0.05):
        v = float(np.nansum(t.w * t.h))
        if v > best_v:
            best, best_v = t.id, v
    return best


def focus_timeline(
    scene: Scene, turns: list[Turn], mapping: dict[str, tuple[int, float]]
) -> list[tuple[float, float, int]]:  # fmt: skip
    """Which track the camera follows over time, with the 0.4 s onset delay and 1.2 s dwell."""
    base = dominant_track(scene)
    if base is None:
        return []
    want = np.full(len(scene.t), base, dtype=int)
    if mapping and len({m[0] for m in mapping.values()}) > 1:
        for t in turns:
            trk = mapping.get(t.speaker)
            if trk is None:
                continue
            sel = (scene.t >= t.start) & (scene.t < t.end)
            want[sel] = trk[0]
    dt = float(np.median(np.diff(scene.t))) if len(scene.t) > 1 else 0.125
    cur, held, pending, pend_for = int(want[0]), 0.0, None, 0.0
    out_id = np.zeros(len(scene.t), dtype=int)
    for k in range(len(scene.t)):
        w = int(want[k])
        present = scene.tracks[w].presence[k] if w in scene.tracks else False
        if w != cur and present:
            pending = w if pending != w else pending
            pend_for = pend_for + dt if pending == w else dt
            if pend_for >= SWITCH_DELAY and held >= MIN_FOCUS_DWELL:
                cur, held, pending, pend_for = w, 0.0, None, 0.0
        else:
            pending, pend_for = None, 0.0
        held += dt
        out_id[k] = cur
    segs: list[tuple[float, float, int]] = []
    start = 0
    for k in range(1, len(out_id) + 1):
        if k == len(out_id) or out_id[k] != out_id[start]:
            segs.append(
                (
                    float(scene.t[start]),
                    float(scene.t[min(k, len(scene.t) - 1)] + (dt if k == len(out_id) else 0)),
                    int(out_id[start]),
                )
            )
            start = k
    return segs


def alternation_rate(timeline: list[tuple[float, float, int]], window: float = 6.0) -> float:
    """Max subject switches per second within any `window`-second window."""
    switches = [b[0] for a, b in pairwise(timeline)]
    best = 0
    for s in switches:
        best = max(best, sum(1 for x in switches if s <= x < s + window))
    return best / window


def detect_facecam(scene: Scene) -> int | None:
    """A small, static, persistent face (a streamer's facecam) while the screen is the content."""
    n = len(scene.t)
    for t in significant_tracks(scene, 0.5):
        wmed, hmed = float(np.nanmedian(t.w)), float(np.nanmedian(t.h))
        cx, cy = np.nanstd(t.cx) / scene.width, np.nanstd(t.cy) / scene.height
        near_edge = (
            min(np.nanmedian(t.cx), scene.width - np.nanmedian(t.cx)) < 0.3 * scene.width
            or min(np.nanmedian(t.cy), scene.height - np.nanmedian(t.cy)) < 0.3 * scene.height
        )
        if (
            wmed < 0.20 * scene.width
            and hmed < 0.18 * scene.height  # a real facecam is small; a talking head is not
            and cx < 0.03
            and cy < 0.03
            and near_edge
            and t.presence.sum() / n >= 0.5
        ):
            return t.id
    return None


def usable_faces(scene: Scene) -> bool:
    n = len(scene.t)
    if not n:
        return False
    with_face = np.mean([any(t.presence[k] for t in scene.tracks.values()) for k in range(n)])
    return bool(with_face >= 0.25)


def gallery(scene: Scene) -> bool:
    """Many simultaneous faces (video-call grid): no crop can do it justice."""
    n = len(scene.t)
    counts = [sum(t.presence[k] for t in scene.tracks.values()) for k in range(n)]
    return bool(counts and np.median(counts) >= 4)


def two_shot_fits(scene: Scene, ids: list[int], crop_w: float) -> bool:
    a, b = scene.tracks[ids[0]], scene.tracks[ids[1]]
    both = a.presence & b.presence
    if both.sum() < max(3, 0.3 * len(scene.t)):
        return False
    lo = np.minimum(a.cx[both] - a.w[both] / 2, b.cx[both] - b.w[both] / 2)
    hi = np.maximum(a.cx[both] + a.w[both] / 2, b.cx[both] + b.w[both] / 2)
    return bool(np.percentile(hi - lo, 90) <= crop_w * 0.88)


def plan_shot(
    scene: Scene, t0: float, t1: float, turns: list[Turn], crop_w: float
) -> LayoutSegment:
    """Layout for one shot [t0, t1) (a shot has no cuts inside it)."""
    sel = (scene.t >= t0) & (scene.t < t1)
    sub = Scene(scene.t[sel], scene.width, scene.height,
                {i: TrackSeries(i, *(getattr(t, k)[sel] for k in ("cx", "cy", "w", "h", "eye_y", "mouth"))) for i, t in scene.tracks.items()})  # fmt: skip
    if len(sub.t) < 2 or not usable_faces(sub) or gallery(sub):
        return LayoutSegment(t0, t1, "fit_blur")
    cam = detect_facecam(sub)
    if cam is not None:
        others = [t for t in significant_tracks(sub) if t.id != cam]
        if not others:
            return LayoutSegment(t0, t1, "screen_cam", cam_track=cam)
    mapping = map_speakers_to_tracks(sub, [t for t in turns if t.end > t0 and t.start < t1])
    timeline = focus_timeline(sub, [t for t in turns if t.end > t0 and t.start < t1], mapping)
    if not timeline:
        return LayoutSegment(t0, t1, "fit_blur")
    ids = sorted({x[2] for x in timeline})
    sig = [t.id for t in significant_tracks(sub) if t.id in ids] or ids[:1]
    if len(sig) >= 2:
        if alternation_rate(timeline) >= 0.5:  # more than one switch per ~2 s
            return LayoutSegment(t0, t1, "stacked", tracks=sig[:2], focus=timeline)
        if two_shot_fits(sub, sig[:2], crop_w):
            return LayoutSegment(t0, t1, "two", tracks=sig[:2], focus=timeline)
    return LayoutSegment(t0, t1, "single", focus=timeline)


def enforce_hysteresis(
    segs: list[LayoutSegment], min_dwell: float = MIN_LAYOUT_DWELL
) -> list[LayoutSegment]:
    """Merge layout runs shorter than `min_dwell` into their longer neighbour."""
    segs = [s for s in segs if s.t1 - s.t0 > 1e-6]
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for k, s in enumerate(segs):
            if s.t1 - s.t0 < min_dwell and not s.override:
                nb = k - 1 if k > 0 else k + 1
                if (
                    k > 0
                    and k + 1 < len(segs)
                    and (segs[k + 1].t1 - segs[k + 1].t0) > (segs[k - 1].t1 - segs[k - 1].t0)
                ):
                    nb = k + 1
                other = segs[nb]
                other.t0, other.t1 = min(other.t0, s.t0), max(other.t1, s.t1)
                del segs[k]
                changed = True
                break
    merged: list[LayoutSegment] = []
    for s in sorted(segs, key=lambda s: s.t0):
        if (
            merged
            and merged[-1].layout == s.layout
            and merged[-1].cam_track == s.cam_track
            and merged[-1].tracks == s.tracks
        ):
            merged[-1].t1 = s.t1
            merged[-1].focus += s.focus
        else:
            merged.append(s)
    return merged


def _complete_override(scene: Scene, o: LayoutSegment) -> None:
    """Fill in what an editor's override needs (which face to follow) from the analysis."""
    o.override = True
    sig = sorted(significant_tracks(scene, 0.05), key=lambda t: -float(np.nansum(t.w * t.h)))
    if o.layout == "single" and sig and not o.focus:
        o.focus = [(o.t0, o.t1, sig[0].id)]
    elif o.layout in ("two", "stacked") and len(sig) >= 2 and not o.tracks:
        o.tracks = [sig[0].id, sig[1].id]
        o.focus = [(o.t0, o.t1, sig[0].id)]
    elif o.layout == "screen_cam" and o.cam_track is None:
        o.cam_track = detect_facecam(scene) or (
            min(sig, key=lambda t: float(np.nanmedian(t.w))).id if sig else None
        )
    if o.layout in ("single", "two", "stacked") and (
        not o.focus or (o.layout != "single" and len(o.tracks) < 2 and o.layout != "two")
    ):
        if o.layout != "single" and len(o.tracks) < 2:
            o.layout = (
                "single" if sig else "fit_blur"
            )  # not enough faces for a split: degrade gracefully
            o.focus = [(o.t0, o.t1, sig[0].id)] if sig else []
    if o.layout == "screen_cam" and o.cam_track is None:
        o.layout = "fit_blur"


def build_plan(
    scene: Scene, shots: list[tuple[float, float]], turns: list[Turn], crop_w: float,
    overrides: list[LayoutSegment] | None = None,
) -> list[LayoutSegment]:  # fmt: skip
    """Layout per shot, merged with hysteresis; manual per-segment overrides win."""
    segs = [plan_shot(scene, a, b, turns, crop_w) for a, b in shots if b - a > 1e-6]
    segs = enforce_hysteresis(segs)
    for o in overrides or []:
        _complete_override(scene, o)
        out: list[LayoutSegment] = []
        for s in segs:
            if s.t1 <= o.t0 or s.t0 >= o.t1:
                out.append(s)
                continue
            if s.t0 < o.t0:
                out.append(LayoutSegment(s.t0, o.t0, s.layout, s.focus, s.cam_track, s.tracks))
            if s.t1 > o.t1:
                out.append(LayoutSegment(o.t1, s.t1, s.layout, s.focus, s.cam_track, s.tracks))
        out.append(o)
        segs = sorted(out, key=lambda s: s.t0)
    return segs


def shots_from_cuts(
    t0: float, t1: float, cuts: list[float], min_len: float = 0.3
) -> list[tuple[float, float]]:
    pts = [t0] + [c for c in sorted(cuts) if t0 + min_len < c < t1 - min_len] + [t1]
    return list(pairwise(pts))


def turns_from_words(words: list, t0: float, t1: float, gap: float = 0.6) -> list[Turn]:
    """Diarization turns from per-word speaker labels (contiguous same-speaker words)."""
    by: dict[str, list[tuple[float, float]]] = defaultdict(list)
    cur: tuple[str, float, float] | None = None
    turns: list[Turn] = []
    for w in words:
        if w.end < t0 or w.start > t1 or not w.speaker:
            continue
        if cur and cur[0] == w.speaker and w.start - cur[2] <= gap:
            cur = (cur[0], cur[1], w.end)
        else:
            if cur:
                turns.append(Turn(cur[1], cur[2], cur[0]))
            cur = (w.speaker, w.start, w.end)
    if cur:
        turns.append(Turn(cur[1], cur[2], cur[0]))
    del by
    return turns
