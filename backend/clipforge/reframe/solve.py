"""Turn a layout plan into per-output-frame crop rectangles (through the EDL)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from clipforge.edl import EDL
from clipforge.reframe import camera as cam
from clipforge.reframe.camera import PATH_FPS, Rect
from clipforge.reframe.plan import LayoutSegment, Scene, TrackSeries

OUT_W, OUT_H = 1080, 1920


@dataclass
class FrameSpec:
    layout: str
    rects: list[Rect] = field(default_factory=list)  # source-pixel crops (see layout)
    focus: int | None = None
    cut: bool = False  # a hard camera cut happens at this frame


@dataclass
class Solved:
    fps: float
    frames: list[FrameSpec]
    cut_frames: list[int]
    x_frac: np.ndarray  # crop-centre x as a fraction of frame width (single layouts), per frame

    def jerk(self) -> dict[str, float]:
        return cam.path_jerk(self.x_frac, self.fps, self.cut_frames)


def _track_paths(
    scene: Scene, grid: np.ndarray, ids: list[int], aspect: float, zoom_cap: float
) -> dict[int, dict[str, np.ndarray]]:  # fmt: skip
    """Follow-paths (centre x, eye y) plus a per-track zoom for every track in `ids`."""
    out: dict[int, dict[str, np.ndarray]] = {}
    dt = 1.0 / PATH_FPS
    for i in ids:
        tr: TrackSeries = scene.tracks[i]
        med_h = float(np.nanmedian(tr.h)) if tr.presence.any() else scene.height * 0.2
        med_eye = float(np.nanmedian(tr.eye_y)) if tr.presence.any() else scene.height * 0.4
        z = min(cam.zoom_for_face(med_h, med_eye, scene.height), zoom_cap)
        cw, ch = cam.crop_size(scene.width, scene.height, aspect, z)
        cx = cam.resample(scene.t, cam.fill_gaps(tr.cx, scene.width / 2), grid)
        ey = cam.resample(scene.t, cam.fill_gaps(tr.eye_y, scene.height * 0.4), grid)
        bias = cam.lead_room_bias(
            float(np.nanmedian(tr.cx)) if tr.presence.any() else scene.width / 2, scene.width, cw
        )
        out[i] = {
            "x": cam.follow(cx, dt, cw) + bias,
            "y": cam.follow(ey, dt, ch, sigma_s=0.6, dead_zone=cam.VERTICAL_DEAD_ZONE),
            "cw": np.full(len(grid), cw),
            "ch": np.full(len(grid), ch),
        }
    return out


def _rect_at(paths: dict[str, np.ndarray], k: int, sw: int, sh: int) -> Rect:
    cw, ch = float(paths["cw"][k]), float(paths["ch"][k])
    x0 = float(np.clip(paths["x"][k] - cw / 2, 0, sw - cw))
    y0 = float(np.clip(paths["y"][k] - cam.EYE_LINE * ch, 0, sh - ch))
    return Rect(x0, y0, cw, ch)


def solve(
    scene: Scene, plan: list[LayoutSegment], edl: EDL, out_fps: float, aspect: float = OUT_W / OUT_H,
    punch_in: float = 1.0, max_zoom: float = cam.MAX_ZOOM,
) -> Solved:  # fmt: skip
    """Per-output-frame layout and crops. Paths are computed in source time, then EDL-remapped.

    `punch_in` > 1 (e.g. 1.08) zooms every second kept segment by that factor around the same
    centre so filler/pause jump cuts read as deliberate editing (single and two layouts only).
    """
    t_lo, t_hi = float(scene.t[0]), float(scene.t[-1] + 1.0 / max(1.0, PATH_FPS))
    grid = np.arange(t_lo, t_hi, 1.0 / PATH_FPS)
    sw, sh = scene.width, scene.height
    seg_data: list[dict] = []
    for seg in plan:
        d: dict = {"seg": seg, "cuts": []}
        if seg.layout == "single":
            ids = sorted({x[2] for x in seg.focus})
            paths = _track_paths(scene, grid, ids, aspect, max_zoom)
            intervals = [(a, b, i) for a, b, i in seg.focus]
            xs, cuts = cam.stitch_focus(grid, intervals, {i: paths[i]["x"] for i in ids}, sw)
            ys, _ = cam.stitch_focus(grid, intervals, {i: paths[i]["y"] for i in ids}, sh * 4)
            cws, _ = cam.stitch_focus(grid, intervals, {i: paths[i]["cw"] for i in ids}, sw * 4)
            chs, _ = cam.stitch_focus(grid, intervals, {i: paths[i]["ch"] for i in ids}, sw * 4)
            d.update(x=xs, y=ys, cw=cws, ch=chs, cuts=cuts, paths=paths, intervals=intervals)
        elif seg.layout == "two":
            a, b = seg.tracks
            pa, pb = _track_paths(scene, grid, [a, b], aspect, 1.0).values()
            cw, ch = cam.crop_size(sw, sh, aspect, 1.0)
            mid = (pa["x"] + pb["x"]) / 2
            d.update(
                x=cam.follow(mid, 1 / PATH_FPS, cw),
                y=(pa["y"] + pb["y"]) / 2,
                cw=np.full(len(grid), cw),
                ch=np.full(len(grid), ch),
            )
        elif seg.layout == "stacked":
            half_aspect = OUT_W / (OUT_H / 2)
            tp = _track_paths(scene, grid, list(seg.tracks), half_aspect, max_zoom)
            d.update(stack=tp)
        elif seg.layout == "screen_cam" and seg.cam_track is not None:
            tr = scene.tracks[seg.cam_track]
            fx, fy = float(np.nanmedian(tr.cx)), float(np.nanmedian(tr.eye_y))
            fw = float(np.nanmedian(tr.w))
            cam_aspect = OUT_W / (OUT_H - round(OUT_W * sh / sw))
            cw = min(fw / 0.45, sw, sh * cam_aspect)
            ch = cw / cam_aspect
            d.update(
                cam_rect=Rect(
                    float(np.clip(fx - cw / 2, 0, sw - cw)),
                    float(np.clip(fy - cam.EYE_LINE * ch, 0, sh - ch)),
                    cw,
                    ch,
                )
            )
        seg_data.append(d)

    n = round(edl.duration * out_fps)
    frames: list[FrameSpec] = []
    x_frac = np.zeros(n)
    cut_frames: list[int] = []
    prev_focus_cut = -1
    for f in range(n):
        ts = edl.out_to_src((f + 0.5) / out_fps)
        if not seg_data:
            frames.append(FrameSpec("fit_blur"))
            continue
        cur: dict = next((s for s in seg_data if s["seg"].t0 <= ts < s["seg"].t1), seg_data[-1])
        seg: LayoutSegment = cur["seg"]
        k = int(np.clip(np.searchsorted(grid, ts), 0, len(grid) - 1))
        if seg.layout in ("single", "two"):
            r = _rect_at(
                {"x": cur["x"], "y": cur["y"], "cw": cur["cw"], "ch": cur["ch"]}, k, sw, sh
            )
            spec = FrameSpec(
                seg.layout, [r], focus=next((i for a, b, i in seg.focus if a <= ts < b), None)
            )
            x_frac[f] = (r.x + r.w / 2) / sw
            if (
                seg.layout == "single"
                and any(abs(c - k) <= 1 for c in cur["cuts"])
                and prev_focus_cut != k
            ):
                spec.cut = True
                prev_focus_cut = k
        elif seg.layout == "stacked":
            rects = [_rect_at(cur["stack"][i], k, sw, sh) for i in seg.tracks]
            spec = FrameSpec("stacked", rects)
            x_frac[f] = x_frac[f - 1] if f else 0.5
        elif seg.layout == "screen_cam" and "cam_rect" in cur:
            spec = FrameSpec("screen_cam", [Rect(0, 0, sw, sh), cur["cam_rect"]])
            x_frac[f] = 0.5
        else:
            spec = FrameSpec("fit_blur", [Rect(0, 0, sw, sh)])
            x_frac[f] = 0.5
        frames.append(spec)
    if punch_in > 1.0 and len(edl.segments) > 1:
        for f, spec in enumerate(frames):
            k = next(
                (
                    i
                    for i, s in enumerate(edl.segments)
                    if s.out_in <= (f + 0.5) / out_fps < s.out_out
                ),
                0,
            )
            if k % 2 == 1 and spec.layout in ("single", "two") and spec.rects:
                r = spec.rects[0]
                nw, nh = r.w / punch_in, r.h / punch_in
                cx, cy = r.x + r.w / 2, r.y + r.h / 2
                spec.rects[0] = Rect(
                    min(max(cx - nw / 2, 0), sw - nw), min(max(cy - nh / 2, 0), sh - nh), nw, nh
                )
    # Hard cuts: layout changes, EDL joins and subject cuts all break path continuity.
    for f in range(1, n):
        if frames[f].layout != frames[f - 1].layout or frames[f].cut:
            cut_frames.append(f)
    for c in edl.cut_points_out:
        cut_frames.append(round(c * out_fps))
    return Solved(out_fps, frames, sorted(set(cut_frames)), x_frac)
