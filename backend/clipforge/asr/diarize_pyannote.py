"""Speaker diarization with pyannote.audio (needs an HF token with accepted terms).

Pipeline id and package version are verified against the installed pyannote.audio; see
ADR-003. Imported lazily so the base install stays light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipforge.asr.diarize import Turn
from clipforge.config import Settings
from clipforge.errors import ASRError
from clipforge.procs import CancelToken

PIPELINE_ID = "pyannote/speaker-diarization-community-1"


def diarize_wav(wav: Path, settings: Settings, cancel: CancelToken) -> list[Turn]:
    try:
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise ASRError(
            "Speaker diarization is not installed.", "Run: uv sync --extra diarization"
        ) from e
    assert settings.hf_token is not None
    try:
        pipe = Pipeline.from_pretrained(PIPELINE_ID, token=settings.hf_token.get_secret_value())
    except Exception as e:
        raise ASRError(
            "Could not load the diarization model (access denied or offline).",
            f"Accept the terms at https://huggingface.co/{PIPELINE_ID} with your token's "
            "account, then retry. Continuing without speaker labels is also fine.",
        ) from e
    if pipe is None:
        raise ASRError("The diarization model returned nothing.", "Check your HF token access.")
    runner: Any = pipe
    result: Any = _run_on_best_device(runner, wav)
    ann: Any = getattr(result, "speaker_diarization", result)
    return [Turn(seg.start, seg.end, spk) for seg, _, spk in ann.itertracks(yield_label=True)]


def load_waveform(wav: Path) -> dict[str, Any]:
    """Our 16 kHz mono WAV as an in-memory waveform. pyannote then never decodes a file itself, which
    would need torchcodec and FFmpeg's shared libraries (the usual failure on a fresh Mac)."""
    import wave

    import numpy as np
    import torch

    with wave.open(str(wav), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ASRError("Unexpected audio format for speaker identification.", "Retry the import.")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    x = x.reshape(-1, ch).T  # (channels, samples)
    return {"waveform": torch.from_numpy(np.ascontiguousarray(x)), "sample_rate": rate}


def _run_on_best_device(pipe: Any, wav: Path) -> Any:
    """Prefer Apple's MPS GPU or CUDA; fall back to CPU if a device op is unsupported."""
    import torch

    audio = load_waveform(wav)
    device = (
        "mps"
        if torch.backends.mps.is_available()
        else "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )
    if device != "cpu":
        try:
            pipe.to(torch.device(device))
            return pipe(audio)
        except Exception:
            pipe.to(torch.device("cpu"))
    return pipe(audio)
