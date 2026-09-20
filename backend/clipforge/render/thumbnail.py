"""Thumbnail: pick the best frame of the clip and compose the hook title on it in the template's style."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from clipforge.captions.spec import CaptionTemplate
from clipforge.captions.timeline import FRAME_H, FRAME_W, load_font
from clipforge.edl import EDL
from clipforge.models import VideoInfo
from clipforge.procs import run_streaming
from clipforge.reframe.solve import Solved
from clipforge.render.video import compose, decode_filter


def _grab(master: Path, video: VideoInfo, ts: float, tonemap_ok: bool) -> np.ndarray | None:
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(master), "-map", "0:v:0", "-frames:v", "1",
            "-vf", decode_filter(video, 25.0, tonemap_ok), "-f", "rawvideo", "-"]  # fmt: skip
    raw = subprocess.run(argv, capture_output=True, check=False).stdout
    need = video.width * video.height * 3
    return (
        np.frombuffer(raw[:need], dtype=np.uint8).reshape(video.height, video.width, 3)
        if len(raw) >= need
        else None
    )


def pick_frame(
    master: Path,
    video: VideoInfo,
    edl: EDL,
    solved: Solved,
    work: Path,
    tonemap_ok: bool,
    candidates: int = 16,
) -> dict:
    """Compose candidate frames (same crops as the video, no captions), score them, return the best row."""
    import cv2

    d = work / "thumb_candidates"
    d.mkdir(exist_ok=True)
    cuts = edl.cut_points_out
    dur = edl.duration
    times = [
        t
        for t in np.linspace(0.6, max(dur - 0.6, 0.7), candidates)
        if all(abs(t - c) > 0.2 for c in cuts)
    ]
    paths = []
    for k, t in enumerate(times):
        frame = _grab(master, video, edl.out_to_src(t), tonemap_ok)
        if frame is None:
            continue
        idx = min(int(t * solved.fps), len(solved.frames) - 1)
        p = d / f"c{k:02d}_{t:.2f}.png"
        cv2.imwrite(str(p), compose(frame, solved.frames[idx]))
        paths.append(str(p))
    out = d / "scores.json"
    code, tail = run_streaming(
        [sys.executable, "-m", "clipforge.render.thumb_score", str(out), *paths]
    )
    if code != 0:
        raise RuntimeError("thumbnail scoring failed: " + tail[-200:])
    rows = json.loads(out.read_text())
    best = max(rows, key=lambda r: r["total"])
    best["candidates"] = len(rows)
    return best


def _title_y(block_h: float, face_box: list[int] | None) -> float:
    """Top of the title block: in the larger free band above or below the face (never over it)."""
    if not face_box:
        return FRAME_H * 0.20 - block_h / 2
    top, bottom = face_box[1], face_box[1] + face_box[3]
    above, below = top - FRAME_H * 0.05, FRAME_H * 0.86 - bottom
    if above >= block_h * 1.15 or above >= below:
        return (
            max(FRAME_H * 0.05, top - block_h - FRAME_H * 0.015)
            if above >= block_h
            else FRAME_H * 0.05
        )
    return min(bottom + FRAME_H * 0.02, FRAME_H * 0.86 - block_h)


def compose_title(
    image_path: Path, text: str, tpl: CaptionTemplate, out: Path, face_box: list[int] | None = None
) -> Path:
    """Draw the hook on the chosen frame in the template's hook style, as a 1080x1920 JPEG."""
    img = Image.open(image_path).convert("RGB").resize((FRAME_W, FRAME_H))
    if text:
        h = tpl.hook
        font_spec = h.font or tpl.font
        font = load_font(font_spec.file, round(h.size * FRAME_W * 1.25), font_spec.weight)
        words = (text.upper() if h.case == "upper" else text).split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cur and font.getlength(trial) > FRAME_W * 0.84:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
        line_h = font.size * 1.15
        block_h = line_h * len(lines)
        y = _title_y(block_h, face_box)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if h.box:
            pad = h.box.pad_x * FRAME_W
            w_max = max(font.getlength(x) for x in lines)
            rgb = tuple(int(h.box.color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            fill = (*rgb, round(255 * min(h.box.opacity + 0.15, 1)))
            d.rounded_rectangle(
                [
                    (FRAME_W - w_max) / 2 - pad,
                    y - pad * 0.6,
                    (FRAME_W + w_max) / 2 + pad,
                    y + block_h + pad * 0.4,
                ],
                radius=h.box.radius * FRAME_W,
                fill=fill,
            )
        stroke = max(round(h.stroke.width * FRAME_W * 1.3), 4 if not h.box else 0)
        for i, line in enumerate(lines):
            d.text(
                (FRAME_W / 2, y + line_h * (i + 0.5)),
                line,
                font=font,
                fill=h.fill,
                anchor="mm",
                stroke_width=stroke,
                stroke_fill=h.stroke.color,
            )
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    img.save(out, "JPEG", quality=92)
    return out
