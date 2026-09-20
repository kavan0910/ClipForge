"""Remotion renderer: unique visual states rendered once, composited as alpha layers in the single encode.

Chromium screenshots dominate the cost, and most caption frames are identical (only the active word
and short animations change). So we render each distinct state once (`states` mode of captions/render.mjs)
and hand ffmpeg a concat list that holds every still for the right duration (ADR-006).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipforge.captions.spec import CaptionTemplate
from clipforge.captions.timeline import FRAME_H, Timeline
from clipforge.errors import ClipforgeError
from clipforge.procs import CancelToken, run_streaming

CAPTIONS_DIR = Path(__file__).resolve().parents[3] / "captions"


@dataclass
class Layer:
    name: str  # captions | hook
    concat: Path  # ffconcat list of PNG stills with durations
    y0: int  # where the band sits in the 1080x1920 frame
    height: int
    states: int


def node_available() -> bool:
    return (
        shutil.which("node") is not None and (CAPTIONS_DIR / "node_modules" / "remotion").exists()
    )


def _active_word(chunk, t: float) -> int:
    for i, w in enumerate(chunk.words):
        end = chunk.words[i + 1].start if i + 1 < len(chunk.words) else chunk.end
        if w.start <= t < end:
            return i
    return -1


def unique_states(
    tl: Timeline, tpl: CaptionTemplate, layer: str, fps: float = 30.0
) -> tuple[list[float], list[int]]:
    """(state times, per-frame state index). Frames with the same visible state share one render."""
    n = max(round(tl.duration * fps), 1)
    m = tpl.motion
    times: list[float] = []
    seen: dict[tuple, int] = {}
    frame_state: list[int] = []
    for f in range(n):
        t = f / fps
        chunk = (
            next((c for c in tl.chunks if c.start <= t < c.end), None)
            if layer == "captions"
            else None
        )
        hook_on = bool(layer == "hook" and tl.hook and tl.hook.start <= t < tl.hook.end)
        animating = bool(
            chunk
            and m.kind != "none"
            and (
                (t - chunk.start) * 1000 < m.ms
                or (m.out_kind == "fade" and (chunk.end - t) * 1000 < m.out_ms)
            )
        )
        hook_anim = bool(
            hook_on and tl.hook and (t - tl.hook.start < 0.16 or tl.hook.end - t < 0.16)
        )
        key: tuple = (
            chunk.id if chunk else -1,
            _active_word(chunk, t) if chunk else -1,
            hook_on,
            f if (animating or hook_anim) else None,
        )
        if key not in seen:
            seen[key] = len(times)
            times.append(t)
        frame_state.append(seen[key])
    return times, frame_state


def band_for(tpl: CaptionTemplate, layer: str) -> tuple[int, int]:
    """Vertical band (y0, height) in the 1080x1920 frame that can contain the layer."""
    if layer == "hook":
        return max(round(tpl.hook.y * FRAME_H - 0.13 * FRAME_H), 0), round(0.26 * FRAME_H)
    return max(round(tpl.anchor.y * FRAME_H - 0.15 * FRAME_H), 0), round(0.30 * FRAME_H)


def render_layer(
    tl: Timeline,
    tpl: CaptionTemplate,
    work: Path,
    layer: str,
    fps: float = 30.0,
    cancel: CancelToken | None = None,
) -> Layer | None:
    if layer == "hook" and not tl.hook:
        return None
    if layer == "captions" and not tl.chunks:
        return None
    times, frame_state = unique_states(tl, tpl, layer, fps)
    y0, h = band_for(tpl, layer)
    d = work / f"layer_{layer}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "timeline.json").write_text(tl.model_dump_json())
    (d / "template.json").write_text(tpl.model_dump_json())
    (d / "times.json").write_text(json.dumps(times))
    env = {
        **os.environ,
        "CAPTION_TIMES": str(d / "times.json"),
        "CAPTION_CONCURRENCY": os.environ.get("CAPTION_CONCURRENCY", "3"),
    }
    argv = [
        "node",
        str(CAPTIONS_DIR / "render.mjs"),
        str(d / "timeline.json"),
        str(d / "template.json"),
        str(d / "png"),
        "states",
        str(fps),
        layer,
        str(y0),
        str(h),
    ]
    code, tail = run_streaming(argv, cancel=cancel, env=env)
    if code != 0:
        raise ClipforgeError("Remotion could not render the captions.", tail[-300:])
    pngs = sorted((d / "png").glob("element-*.png"))
    if len(pngs) != len(times):
        raise ClipforgeError(
            "Remotion produced an unexpected number of frames.",
            f"expected {len(times)}, got {len(pngs)}",
        )
    # ffconcat: hold each still from its first frame until the next state begins.
    lines = ["ffconcat version 1.0"]
    runs: list[tuple[int, int]] = []
    for s in frame_state:
        if runs and runs[-1][0] == s:
            runs[-1] = (s, runs[-1][1] + 1)
        else:
            runs.append((s, 1))
    for s, count in runs:
        lines += [f"file '{pngs[s].as_posix()}'", f"duration {count / fps:.6f}"]
    lines.append(
        f"file '{pngs[runs[-1][0]].as_posix()}'"
    )  # concat quirk: last file repeated without duration
    concat = d / "layer.ffconcat"
    concat.write_text("\n".join(lines) + "\n")
    return Layer(layer, concat, y0, h, len(times))


def preview_png(layer: Layer, out: Path) -> Path:
    """First still of a layer composited on grey, for quick visual checks."""
    first = next(iter(layer.concat.parent.joinpath("png").glob("element-*.png")))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x556677:s=1080x1920", "-i", str(first),
                    "-filter_complex", f"[0][1]overlay=0:{layer.y0}", "-frames:v", "1", str(out)], check=True)  # fmt: skip
    return out
