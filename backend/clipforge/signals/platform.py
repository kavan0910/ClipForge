"""Platform signals -> per-second arrays: most-replayed heatmap, live-chat rate."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np


def heatmap_per_second(heatmap: Sequence[dict], duration: float) -> list[float]:
    """Resample yt-dlp's [{start_time, end_time, value}] to 1 Hz, normalised to max 1."""
    n = int(np.ceil(duration))
    out = np.zeros(n)
    for seg in heatmap:
        a, b = float(seg.get("start_time", 0)), float(seg.get("end_time", 0))
        out[max(int(a), 0) : min(int(np.ceil(b)), n)] = float(seg.get("value", 0))
    top = out.max() if n else 0
    return (out / top).tolist() if top > 0 else out.tolist()


def chat_rate_per_second(chat_file: Path, duration: float, window: int = 10) -> list[float]:
    """Messages per second from a yt-dlp `live_chat` json-lines file, smoothed over `window` s."""
    n = int(np.ceil(duration))
    counts = np.zeros(n)
    for line in chat_file.read_text(errors="ignore").splitlines():
        try:
            actions = json.loads(line)["replayChatItemAction"]
            offset_ms = int(actions["videoOffsetTimeMsec"])
        except (KeyError, ValueError, TypeError):
            continue
        sec = offset_ms // 1000
        if 0 <= sec < n:
            counts[sec] += 1
    kernel = np.ones(window) / window
    return np.convolve(counts, kernel, mode="same").tolist()
