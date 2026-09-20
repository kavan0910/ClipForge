"""AI super-resolution (Real-ESRGAN general-x4v3, a compact 4x model) for crops that must be enlarged a lot.

A vertical crop of a 1080p frame has only ~608 px of real width but the clip is 1080 px wide, so plain resizing
is soft. The model reconstructs plausible edge detail instead. It runs in its own process (torch on Apple's GPU
via MPS, or CUDA) so it never shares a process with OpenCV/PyAV, and frames travel over pipes.

Protocol on stdin/stdout: request = '<IIII' (w, h, out_w, out_h) + w*h*3 BGR bytes; reply = out_w*out_h*3 BGR bytes.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from clipforge import fetch
from clipforge.errors import MediaError
from clipforge.procs import kill_tree

MODEL_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
)
MODEL_SIZE = 4_885_111
MODEL_PATH = Path.home() / ".cache" / "clipforge" / "models" / "realesr-general-x4v3.pth"
HEADER = struct.Struct("<IIII")


def ensure_sr_model(downloader=None) -> Path:
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size == MODEL_SIZE:
        return MODEL_PATH
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    part = MODEL_PATH.with_suffix(".part")
    try:
        (downloader or fetch.download)(MODEL_URL, part)
        if part.stat().st_size != MODEL_SIZE:
            raise OSError(f"unexpected size {part.stat().st_size}")
        part.replace(MODEL_PATH)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise MediaError(
            "Could not download the AI upscaling model.", f"Check your connection and retry. ({e})"
        ) from e
    return MODEL_PATH


def available_device() -> str | None:
    """'mps' or 'cuda' when torch can use a GPU here, else None (a CPU is far too slow for video)."""
    try:
        import torch
    except ImportError:
        return None
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else None


class Upscaler:
    """Client side: owns the worker process."""

    def __init__(self) -> None:
        ensure_sr_model()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "clipforge.render.sr_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
        )  # fmt: skip

    def upscale(self, crop: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        h, w = crop.shape[:2]
        need = out_w * out_h * 3
        try:
            self.proc.stdin.write(
                HEADER.pack(w, h, out_w, out_h) + np.ascontiguousarray(crop).tobytes()
            )
            self.proc.stdin.flush()
            buf = self.proc.stdout.read(need)
        except BrokenPipeError:
            buf = b""
        if len(buf) != need:
            raise MediaError(
                "The AI upscaler stopped unexpectedly.",
                "Retry, or set RENDER_UPSCALE=off in Settings.",
            )
        return np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3).copy()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        finally:
            if self.proc.poll() is None:
                kill_tree(self.proc, 2.0)


def enlargement(frames, out_h: int = 1920) -> float:
    """Typical enlargement factor of the crops in a solved clip (median over frames)."""
    vals = []
    for f in frames:
        if not f.rects:
            continue
        target = out_h / 2 if f.layout == "stacked" else out_h
        if f.layout in ("single", "two", "stacked"):
            vals.append(target / max(f.rects[0].h, 1.0))
    return float(np.median(vals)) if vals else 1.0
