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
    result: Any = runner(str(wav))
    ann: Any = getattr(result, "speaker_diarization", result)
    return [Turn(seg.start, seg.end, spk) for seg, _, spk in ann.itertracks(yield_label=True)]
