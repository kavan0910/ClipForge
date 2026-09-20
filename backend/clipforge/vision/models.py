"""Face-detector model files (YuNet and SCRFD-500M), fetched on first use with a size check."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path

from clipforge import fetch
from clipforge.errors import MediaError

MODELS = Path.home() / ".cache" / "clipforge" / "models"
YUNET = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    232_589,
)
BUFFALO = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip",
    14_969_382,
)
SCRFD_MIN = 2_000_000


def _get(url: str, size: int, dest: Path, downloader: Callable[[str, Path], None]) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        downloader(url, part)
        if part.stat().st_size != size:
            raise OSError(f"unexpected size {part.stat().st_size} (expected {size})")
        part.replace(dest)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise MediaError(
            f"Could not download the face model {dest.name}.",
            f"Check your internet connection and retry. ({e})",
        ) from e


def ensure_face_models(
    directory: Path | None = None, downloader: Callable[[str, Path], None] | None = None
) -> None:
    d = directory or MODELS
    d.mkdir(parents=True, exist_ok=True)
    dl = downloader or fetch.download
    yunet = d / "yunet.onnx"
    if not yunet.exists() or yunet.stat().st_size != YUNET[1]:
        _get(*YUNET, yunet, dl)
    det = d / "buffalo_sc" / "det_500m.onnx"
    if not det.exists() or det.stat().st_size < SCRFD_MIN:
        z = d / "buffalo_sc.zip"
        _get(*BUFFALO, z, dl)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(d / "buffalo_sc")
        if not det.exists():
            raise MediaError(
                "The face model archive was not what we expected.",
                "Delete ~/.cache/clipforge/models and retry.",
            )
