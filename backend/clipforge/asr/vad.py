"""Speech-region detection with Silero VAD (bundled with faster-whisper, run via onnxruntime)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 16000
WINDOW_S = 600  # process long files in 10-minute windows to keep memory flat


def speech_regions(
    wav_path: Path, threshold: float = 0.35, min_speech_ms: int = 150, pad_ms: int = 200
) -> list[tuple[float, float]]:
    """Speech intervals (seconds). Permissive on purpose: it only has to reject non-speech."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(
        threshold=threshold,
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=300,
        speech_pad_ms=pad_ms,
    )
    regions: list[tuple[float, float]] = []
    with wave.open(str(wav_path), "rb") as w:
        n = w.getnframes()
        for start in range(0, n, WINDOW_S * RATE):
            w.setpos(start)
            raw = w.readframes(min(WINDOW_S * RATE, n - start))
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            for ts in get_speech_timestamps(audio, opts):
                regions.append(((start + ts["start"]) / RATE, (start + ts["end"]) / RATE))
    return _merge(regions)


def _merge(regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
