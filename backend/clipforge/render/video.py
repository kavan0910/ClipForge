"""Frame composition and the single video encode.

The source is decoded once, each output frame is composed in Python from the solved crops
(Lanczos), and frames stream into ONE ffmpeg encode that also muxes the processed audio, so the
video is compressed exactly once. See ADR-004 for why frames are composed outside ffmpeg.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from fractions import Fraction
from pathlib import Path

import numpy as np

from clipforge.edl import EDL
from clipforge.errors import MediaError
from clipforge.models import VideoInfo
from clipforge.procs import Cancelled, CancelToken, kill_tree
from clipforge.reframe.camera import Rect
from clipforge.reframe.solve import OUT_H, OUT_W, FrameSpec, Solved

HDR_CHAIN = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"


def fps_arg(fps: float) -> str:
    """ffmpeg rate argument. Probed rates are rounded to 3 decimals, so snap NTSC rates
    (23.976, 29.97, 59.94...) back to their exact fractions; otherwise use a plain fraction."""
    for base in (24, 30, 48, 60, 120):
        if abs(fps - base * 1000 / 1001) < 0.01:
            return f"{base * 1000}/1001"
    f = Fraction(fps).limit_denominator(1001)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def decode_filter(video: VideoInfo, fps: float, tonemap_ok: bool) -> str:
    parts = []
    if video.hdr and tonemap_ok:
        parts.append(HDR_CHAIN)
    parts.append(f"fps={fps_arg(fps)}")
    matrix = "bt709" if (video.height >= 720 or video.hdr) else "bt601"
    parts.append(f"scale=in_range=tv:in_color_matrix={matrix},format=bgr24")
    return ",".join(parts)


def decode_segment(
    master: Path, video: VideoInfo, start: float, n_frames: int, fps: float, tonemap_ok: bool
) -> Iterator[np.ndarray]:
    """Yield exactly `n_frames` BGR frames starting at source time `start` (frame-accurate seek)."""
    frame_bytes = video.width * video.height * 3
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.6f}", "-i", str(master), "-map", "0:v:0",
            "-frames:v", str(n_frames), "-vf", decode_filter(video, fps, tonemap_ok), "-f", "rawvideo", "-"]  # fmt: skip
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True
    )
    last: np.ndarray | None = None
    got = 0
    try:
        assert proc.stdout is not None
        while got < n_frames:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            last = np.frombuffer(raw, dtype=np.uint8).reshape(video.height, video.width, 3)
            got += 1
            yield last
        while got < n_frames and last is not None:  # source ended a hair early: hold the last frame
            got += 1
            yield last
    finally:
        if proc.poll() is None:
            kill_tree(proc, 1.0)
        proc.wait()


def _warp(frame: np.ndarray, r: Rect, w: int, h: int) -> np.ndarray:
    """Crop `r` (whole source pixels) and resize with Lanczos.

    cv2.warpAffine + Lanczos (sub-pixel crops) ran at 35 fps; separable cv2.resize + Lanczos runs
    at ~210 fps. Positions are therefore quantised to a source pixel (<= 0.5 px, about 0.9 px
    in the 1080-wide output), which is invisible at the speeds the camera moves (ADR-004).
    """
    import cv2

    fh, fw = frame.shape[:2]
    cw, ch = min(max(round(r.w), 2), fw), min(max(round(r.h), 2), fh)
    x0, y0 = min(max(round(r.x), 0), fw - cw), min(max(round(r.y), 0), fh - ch)
    out = cv2.resize(frame[y0 : y0 + ch, x0 : x0 + cw], (w, h), interpolation=cv2.INTER_LANCZOS4)
    return sharpen_for_upscale(out, h / ch)


def sharpen_for_upscale(img: np.ndarray, scale: float) -> np.ndarray:
    """Enlarging always softens edges; restore them with an unsharp mask sized to how much we enlarged.
    Nothing is done for crops that are not enlarged (a 4K source), and the amount is capped so it never rings."""
    if scale <= 1.15:
        return img
    import cv2

    amount = min(0.35 + 0.45 * (scale - 1.15), 0.85)
    blur = cv2.GaussianBlur(img, (0, 0), 1.3)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def _fit_blur(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2

    fh, fw = frame.shape[:2]
    s_cover = h / fh
    bw = round(fw * s_cover)
    small = cv2.resize(frame, (max(bw // 8, 1), max(h // 8, 1)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    bg = cv2.resize(small, (bw, h), interpolation=cv2.INTER_LINEAR)
    x0 = (bw - w) // 2
    bg = (bg[:, max(x0, 0) : max(x0, 0) + w].astype(np.float32) * 0.45).astype(np.uint8)
    if bg.shape[1] != w:
        bg = cv2.resize(bg, (w, h))
    fit_h = round(w * fh / fw)
    fg = cv2.resize(
        frame, (w, fit_h), interpolation=cv2.INTER_AREA if fw > w else cv2.INTER_LANCZOS4
    )
    y0 = (h - fit_h) // 2
    bg[y0 : y0 + fit_h] = fg
    return bg


def compose(frame: np.ndarray, spec: FrameSpec, w: int = OUT_W, h: int = OUT_H) -> np.ndarray:
    import cv2

    if spec.layout in ("single", "two"):
        return _warp(frame, spec.rects[0], w, h)
    if spec.layout == "stacked":
        top, bot = (
            _warp(frame, spec.rects[0], w, h // 2),
            _warp(frame, spec.rects[1], w, h - h // 2),
        )
        return np.vstack([top, bot])
    if spec.layout == "screen_cam":
        fh, fw = frame.shape[:2]
        top_h = round(w * fh / fw)
        screen = cv2.resize(
            frame, (w, top_h), interpolation=cv2.INTER_AREA if fw > w else cv2.INTER_LANCZOS4
        )
        return np.vstack([screen, _warp(frame, spec.rects[1], w, h - top_h)])
    return _fit_blur(frame, w, h)


def _ass_filter(ass: Path, fonts: Path | None) -> str:
    """libass overlay in the SAME encode (no second video pass). Paths are escaped for the filtergraph."""

    def esc(p: Path) -> str:
        return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    return f"ass=filename={esc(ass)}" + (f":fontsdir={esc(fonts)}" if fonts else "")


def encode_argv(
    out: Path, audio: Path, fps: float, duration: float, fast: bool, w: int = OUT_W, h: int = OUT_H,
    codec_args: list[str] | None = None, ass_path: Path | None = None, fonts_dir: Path | None = None,
    layers: list[tuple[Path, int]] | None = None,
) -> list[str]:  # fmt: skip
    """One ffmpeg process: raw frames + audio (+ alpha caption layers) -> one H.264/AAC file.

    `layers` are (ffconcat list of PNG stills, y offset). They are overlaid before colour conversion, so the
    picture is still compressed exactly once.
    """
    video = codec_args or (
        ["-c:v", "h264_videotoolbox", "-q:v", "65", "-profile:v", "high", "-allow_sw", "1"]
        if fast
        else ["-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "15",
              "-x264-params", "colorprim=bt709:transfer=bt709:colmatrix=bt709:fullrange=off"]
    )  # fmt: skip
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", fps_arg(fps), "-i", "-",
            "-i", str(audio)]  # fmt: skip
    for concat, _ in layers or []:
        argv += ["-f", "concat", "-safe", "0", "-i", str(concat)]
    final = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    if layers:
        chain = ["[0:v]" + (_ass_filter(ass_path, fonts_dir) if ass_path else "null") + "[b0]"]
        for k, (_, y) in enumerate(layers):
            chain.append(f"[b{k}][{k + 2}:v]overlay=0:{y}:format=auto:eof_action=pass[b{k + 1}]")
        chain.append(f"[b{len(layers)}]{final}[vout]")
        argv += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "1:a"]
    else:
        vf = (_ass_filter(ass_path, fonts_dir) + "," if ass_path else "") + final
        argv += ["-map", "0:v", "-map", "1:a", "-vf", vf]
    return [*argv, *video, "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-color_range", "tv", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}", "-movflags", "+faststart", str(out)]  # fmt: skip


PREVIEW_W, PREVIEW_H = 360, 640


def _start_preview(path: Path, audio: Path, fps: float, frames: int) -> subprocess.Popen:
    """Small caption-free proxy of the composed clip: the editor plays it under the live caption preview."""
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{PREVIEW_W}x{PREVIEW_H}",
            "-r", fps_arg(fps), "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-t", f"{frames / fps:.6f}", "-movflags", "+faststart", str(path.with_suffix(".partial.mp4"))]  # fmt: skip
    return subprocess.Popen(
        argv, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True
    )


def _finish_preview(proc: subprocess.Popen, path: Path) -> None:
    if proc.stdin:
        proc.stdin.close()
    proc.wait()
    if proc.returncode == 0:
        path.with_suffix(".partial.mp4").replace(path)


def render_video(
    master: Path, video: VideoInfo, edl: EDL, solved: Solved, audio: Path, out: Path, fast: bool = False,
    tonemap_ok: bool = True, on_progress: Callable[[float], None] | None = None, cancel: CancelToken | None = None,
    overlay: Callable[[np.ndarray, int], np.ndarray] | None = None, codec_args: list[str] | None = None,
    ass_path: Path | None = None, fonts_dir: Path | None = None,
    layers: list[tuple[Path, int]] | None = None, head: list[np.ndarray] | None = None,
    tail: list[np.ndarray] | None = None, preview: Path | None = None,
) -> None:  # fmt: skip
    """Decode each kept segment, compose every output frame, pipe into one encode."""
    fps = solved.fps
    head, tail = head or [], tail or []
    body = len(solved.frames)
    total = len(head) + body + len(tail)
    proc = subprocess.Popen(encode_argv(out.with_suffix(".partial.mp4"), audio, fps, total / fps, fast, codec_args=codec_args,
                                ass_path=ass_path, fonts_dir=fonts_dir, layers=layers),
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)  # fmt: skip
    written = 0
    prev = _start_preview(preview, audio, fps, total) if preview else None

    def emit(img: np.ndarray) -> None:
        assert proc.stdin is not None
        proc.stdin.write(np.ascontiguousarray(img).tobytes())
        if prev is not None and prev.stdin is not None:
            import cv2

            prev.stdin.write(
                cv2.resize(img, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA).tobytes()
            )

    try:
        assert proc.stdin is not None
        for card in head:
            emit(card)
            written += 1
        for seg in edl.segments:
            f0 = round(seg.out_in * fps)
            f1 = min(round(seg.out_out * fps), body)
            for i, frame in enumerate(
                decode_segment(master, video, seg.src_in, f1 - f0, fps, tonemap_ok)
            ):
                if cancel and cancel.cancelled:
                    raise Cancelled
                img = compose(frame, solved.frames[f0 + i])
                if overlay:
                    img = overlay(img, f0 + i)
                emit(img)
                written += 1
                if on_progress and written % 15 == 0:
                    on_progress(written / total)
        for card in tail:
            emit(card)
            written += 1
        proc.stdin.close()
        if prev is not None and preview is not None:
            _finish_preview(prev, preview)
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise MediaError("The video encode failed.", err[-300:])
    except BrokenPipeError:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise MediaError("The encoder stopped early.", err[-300:]) from None
    finally:
        if proc.poll() is None:
            kill_tree(proc, 2.0)
    out.with_suffix(".partial.mp4").replace(out)
