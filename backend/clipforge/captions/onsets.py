"""Word-onset accuracy: compare transcript word starts with audio energy onsets after a pause.

Words that follow a real pause (>= 250 ms) have an unambiguous acoustic onset: energy rises above the
local noise floor. That gives an independent reference for ASR word timestamps. `refine` snaps the caption
start of such words to the detected onset when it is close, tightening sync without touching timings the
audio cannot confirm.
"""

from __future__ import annotations

import wave
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from clipforge.models import Word

HOP = 0.010


def rms_db_10ms(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as w:
        n = int(w.getframerate() * HOP)
        out = []
        while True:
            raw = w.readframes(n)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            out.append(20 * np.log10(max(float(np.sqrt(np.mean(x * x))) if x.size else 0.0, 1e-7)))
    return np.asarray(out)


def detect_onset(
    rms: np.ndarray, prev_end: float, near: float, span: float = 0.20, min_rise_db: float = 14.0
) -> float | None:
    """50%-rise time of the word starting near `near`, or None when there is no clean onset to measure.

    Needs a quiet stretch right before the search window (so noise, breaths or another speaker do not
    count) and a clearly louder word body: floor = median energy in [near-0.34, near-0.20], body = median
    energy in [near+0.08, near+0.30]. The onset is where energy first stays above their midpoint for 3
    frames inside [near-0.20, near+0.20]. (The midpoint crossing sits ~10-20 ms after the true first
    sound, so this reference is slightly conservative.)
    """
    if prev_end > near - 0.36:
        return None  # not enough silence before the word for a trustworthy floor
    a = lambda t: max(int(t / HOP), 0)  # noqa: E731
    if a(near + 0.30) >= len(rms):
        return None
    floor = float(np.median(rms[a(near - 0.34) : a(near - 0.20)]))
    body = float(np.median(rms[a(near + 0.08) : a(near + 0.30)]))
    if body - floor < min_rise_db:
        return None
    mid = (body + floor) / 2
    for k in range(a(near - span), a(near + span)):
        if rms[k] >= mid and rms[k + 1] >= mid and rms[k + 2] >= mid:
            return k * HOP
    return None


def after_pause(words: Sequence[Word], min_gap: float = 0.36) -> list[int]:
    return [i for i in range(1, len(words)) if words[i].start - words[i - 1].end >= min_gap]


def onset_errors(words: Sequence[Word], rms: np.ndarray) -> list[float]:
    """Detected onset minus transcript start (seconds) for words after a pause."""
    errs = []
    for i in after_pause(words):
        o = detect_onset(rms, words[i - 1].end, words[i].start)
        if o is not None:
            errs.append(o - words[i].start)
    return errs


def summarize(errs: Sequence[float]) -> dict[str, float]:
    a = np.abs(np.asarray(errs))
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "median_ms": round(float(np.median(a)) * 1000, 1), "p90_ms": round(float(np.percentile(a, 90)) * 1000, 1),
            "within_50ms": round(float(np.mean(a <= 0.05)), 3), "signed_median_ms": round(float(np.median(errs)) * 1000, 1)}  # fmt: skip


def refine(words: Sequence[Word], rms: np.ndarray, max_shift: float = 0.12) -> list[Word]:
    """Snap the start of post-pause words to the detected audio onset when it is within `max_shift`."""
    out = [w.model_copy() for w in words]
    for i in after_pause(words):
        o = detect_onset(rms, words[i - 1].end, words[i].start)
        if o is not None and abs(o - words[i].start) <= max_shift and o < words[i].end - 0.02:
            out[i] = out[i].model_copy(update={"start": round(o, 3)})
    return out
