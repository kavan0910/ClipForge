"""Forced alignment (torchaudio MMS_FA): tighter word timings than Whisper's cross-attention estimates.

Whisper word timestamps are DTW guesses; forced alignment against a CTC acoustic model locks each word to
the audio (ADR-003). Words the aligner cannot represent (digits, symbols) keep their Whisper timing.
"""

from __future__ import annotations

import logging
import re
import wave
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from clipforge.models import Sentence, Word

log = logging.getLogger(__name__)
PAD = 0.35  # seconds of context either side of each sentence


def _norm(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower().replace("\u2019", "'"))


def _read(wav_path: Path, start: float, end: float):
    import torch

    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        w.setpos(min(max(int(start * rate), 0), w.getnframes()))
        raw = w.readframes(max(int((end - start) * rate), 1))
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return torch.from_numpy(x)[None], rate


def align_words(
    wav_path: Path, words: Sequence[Word], sentences: Sequence[Sentence], on_progress: Callable[[float], None] | None = None,
    device: str | None = None,
) -> list[Word]:  # fmt: skip
    import torch
    from torchaudio.pipelines import MMS_FA as bundle

    device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    model = bundle.get_model().to(device).eval()
    tokenizer, aligner = bundle.get_tokenizer(), bundle.get_aligner()
    out = [w.model_copy() for w in words]
    for n, s in enumerate(sentences):
        idx = [i for i in range(s.word_lo, s.word_hi + 1) if _norm(words[i].w)]
        if not idx:
            continue
        t0 = max(words[s.word_lo].start - PAD, 0.0)
        t1 = words[s.word_hi].end + PAD
        wave_t, rate = _read(wav_path, t0, t1)
        if wave_t.shape[1] < rate * 0.2:
            continue
        try:
            with torch.inference_mode():
                emission, _ = model(wave_t.to(device))
            spans = aligner(emission[0].cpu(), tokenizer([_norm(words[i].w) for i in idx]))  # pyright: ignore[reportArgumentType]
        except Exception as e:  # one bad sentence must not sink the file
            log.warning("alignment skipped a sentence: %s", e)
            continue
        ratio = wave_t.shape[1] / emission.shape[1] / rate
        for i, sp in zip(idx, spans, strict=True):
            if not sp:
                continue
            a, b = t0 + sp[0].start * ratio, t0 + sp[-1].end * ratio
            if b - a >= 0.02:
                out[i] = out[i].model_copy(update={"start": round(a, 3), "end": round(b, 3)})
        if on_progress and n % 10 == 0:
            on_progress((n + 1) / len(sentences))
    # Keep the timeline valid: monotonic starts, no overlaps, positive durations.
    for k in range(1, len(out)):
        if out[k].start < out[k - 1].end:
            mid = (out[k].start + out[k - 1].end) / 2
            out[k - 1] = out[k - 1].model_copy(
                update={"end": round(max(mid, out[k - 1].start + 0.02), 3)}
            )
            out[k] = out[k].model_copy(update={"start": round(max(mid, out[k - 1].end), 3)})
        if out[k].end <= out[k].start:
            out[k] = out[k].model_copy(update={"end": round(out[k].start + 0.03, 3)})
    return out
