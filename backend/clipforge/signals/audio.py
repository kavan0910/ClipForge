"""Audio signals: energy, speech rate, pause density and laughter/applause tagging (PANNs)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import Path

import numpy as np

from clipforge.errors import MediaError
from clipforge.models import Word
from clipforge.procs import Cancelled, CancelToken

EVENT_RATE = 32000  # PANNs models expect 32 kHz audio
LAUGH = ("Laughter", "Belly laugh", "Chuckle, chortle", "Giggle", "Snicker")
APPLAUSE = ("Applause", "Cheering", "Clapping")


def per_second_energy(rms_db_100ms: Sequence[float], duration: float) -> list[float]:
    """Mean RMS dBFS per second from 100 ms frames (silence floored at -80 dB)."""
    n = int(np.ceil(duration))
    a = np.maximum(np.asarray(rms_db_100ms, dtype=float), -80.0)
    out = np.full(n, -80.0)
    for i in range(n):
        seg = a[i * 10 : (i + 1) * 10]
        if seg.size:
            out[i] = seg.mean()
    return out.tolist()


def speech_rate_and_density(
    words: Sequence[Word], duration: float
) -> tuple[list[float], list[float]]:
    """Words per second (smoothed over 5 s) and speech density (share of time inside phrases)."""
    n = int(np.ceil(duration))
    rate, voiced = np.zeros(n), np.zeros(n)
    for w in words:
        i = min(int(w.start), n - 1)
        rate[i] += 1
    for a, b in pairwise(words):
        pass_gap = b.start - a.end <= 0.35  # inside a phrase
        lo, hi = a.start, (b.start if pass_gap else a.end)
        for sec in range(int(lo), min(int(np.ceil(hi)), n)):
            voiced[sec] += max(0.0, min(hi, sec + 1) - max(lo, sec))
    if words:
        last = words[-1]
        for sec in range(int(last.start), min(int(np.ceil(last.end)), n)):
            voiced[sec] += max(0.0, min(last.end, sec + 1) - max(last.start, sec))
    k = 5
    smoothed = np.convolve(rate, np.ones(k) / k, mode="same")
    return smoothed.tolist(), np.clip(voiced, 0, 1).tolist()


PANNS_DIR = Path.home() / "panns_data"
PANNS_FILES = {  # name -> (url, minimum plausible size in bytes)
    "class_labels_indices.csv": ("https://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv", 10_000),
    "Cnn14_DecisionLevelMax.pth": ("https://zenodo.org/record/3987831/files/Cnn14_DecisionLevelMax_mAP%3D0.385.pth?download=1", 300_000_000),
}  # fmt: skip


def ensure_panns_files(
    directory: Path | None = None, fetch: Callable[[str, Path], None] | None = None
) -> None:
    """Download the PANNs label list and checkpoint with Python (atomic, size-checked), replacing the library's wget calls."""
    directory = directory or PANNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, min_size) in PANNS_FILES.items():
        dest = directory / name
        if dest.exists() and dest.stat().st_size >= min_size:
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            (fetch or _download)(url, part)
            if part.stat().st_size < min_size:
                raise OSError(f"{name} downloaded incompletely ({part.stat().st_size} bytes)")
            part.replace(dest)
        except OSError as e:
            part.unlink(missing_ok=True)
            raise MediaError(
                f"Could not download the sound-event model file {name}.",
                f"Check your internet connection and retry. ({e})",
            ) from e


def _download(url: str, dest: Path) -> None:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


def event_probabilities(
    media_path: Path,
    audio_stream: int,
    duration: float,
    cancel: CancelToken,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[list[float], list[float]]:
    """Per-second laughter and applause probability with PANNs, streamed from the master."""
    ensure_panns_files()  # before the import: the library itself shells out to `wget`, which a fresh Mac lacks
    from panns_inference import SoundEventDetection, labels

    idx = {n: i for i, n in enumerate(labels)}
    laugh_i = [idx[n] for n in LAUGH]
    clap_i = [idx[n] for n in APPLAUSE]
    sed = SoundEventDetection(checkpoint_path=None, device="cpu")
    n = int(np.ceil(duration))
    laugh, clap = np.zeros(n), np.zeros(n)
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(media_path), "-map", f"0:{audio_stream}",
         "-vn", "-ac", "1", "-ar", str(EVENT_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    window = EVENT_RATE * 10
    try:
        assert proc.stdout is not None
        start = 0
        while True:
            raw = proc.stdout.read(window * 2)
            if len(raw) < EVENT_RATE * 2:  # under one second left
                break
            if cancel.cancelled:
                raise Cancelled
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            fr = sed.inference(x[None])[0]  # (frames, 527) at 100 frames per second
            for sec in range(len(x) // EVENT_RATE):
                a = fr[sec * 100 : (sec + 1) * 100]
                if start + sec < n and a.size:
                    laugh[start + sec] = a[:, laugh_i].max()
                    clap[start + sec] = a[:, clap_i].max()
            start += len(x) // EVENT_RATE
            if on_progress:
                on_progress(min(start / max(n, 1), 1.0))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    return laugh.tolist(), clap.tolist()
