"""ASR worker: runs in its own process so it can be cancelled and frees GPU memory on exit.

Usage: python -m clipforge.asr.worker --config config.json
Config: {backend, model, wav, out_dir, chunks:[{index,start,end}], language, prompt}
Each finished chunk is written atomically to out_dir/chunk_NNNN.json; existing chunks are
skipped, so a killed run resumes where it stopped. Progress lines: CFASR|<done>|<total>|<secs>.
"""

from __future__ import annotations

import json
import math
import os
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np

from clipforge.asr.types import RawChunk, RawWord

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}
TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def resolve_model(backend: str, name: str) -> str:
    if backend == "mlx-whisper":
        return MLX_MODELS.get(name, name)  # a full repo id or path is passed through
    return name


def read_slice(wav_path: str, start: float, end: float) -> np.ndarray:
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        w.setpos(min(int(start * rate), w.getnframes()))
        raw = w.readframes(max(int((end - start) * rate), 0))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _finite(value: Any, default: float) -> float:
    """ASR backends occasionally emit NaN/inf scores for degenerate segments."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _words_from_segments(segments: list[dict[str, Any]], offset: float) -> list[RawWord]:
    out: list[RawWord] = []
    for seg in segments:
        for w in seg.get("words") or []:
            token = str(w["word"]).strip()
            if not token:
                continue
            out.append(
                RawWord(
                    w=token,
                    start=offset + float(w["start"]),
                    end=offset + float(w["end"]),
                    prob=_finite(w.get("probability"), 1.0),
                    no_speech=_finite(seg.get("no_speech_prob"), 0.0),
                    logprob=_finite(seg.get("avg_logprob"), 0.0),
                    compression=_finite(seg.get("compression_ratio"), 0.0),
                )
            )
    return out


class MLXRunner:
    def __init__(self, model: str) -> None:
        self.repo = resolve_model("mlx-whisper", model)

    def run(self, audio: np.ndarray, offset: float, language: str | None, prompt: str | None):
        import mlx_whisper

        res = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.repo,
            word_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            language=language,
            temperature=TEMPERATURES,
            compression_ratio_threshold=2.4,
            no_speech_threshold=0.6,
            logprob_threshold=-1.0,
            verbose=None,
        )
        segments: Any = res["segments"]
        return str(res.get("language", language or "en")), _words_from_segments(segments, offset)


class FasterWhisperRunner:
    def __init__(self, model: str) -> None:
        from faster_whisper import WhisperModel

        try:
            import ctranslate2

            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda = False
        self.model = WhisperModel(
            model, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8"
        )

    def run(self, audio: np.ndarray, offset: float, language: str | None, prompt: str | None):
        segs, info = self.model.transcribe(
            audio,
            language=language,
            initial_prompt=prompt or None,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
            temperature=list(TEMPERATURES),
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
        )
        rows = [
            {
                "no_speech_prob": s.no_speech_prob,
                "avg_logprob": s.avg_logprob,
                "compression_ratio": s.compression_ratio,
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                    for w in (s.words or [])
                ],
            }
            for s in segs
        ]
        return info.language, _words_from_segments(rows, offset)


def make_runner(backend: str, model: str):
    if backend == "mlx-whisper":
        return MLXRunner(model)
    if backend == "faster-whisper":
        return FasterWhisperRunner(model)
    raise ValueError(f"unknown ASR backend {backend}")


def _exit_if_orphaned() -> None:
    """Exit when the parent dies (SIGKILL, crash): a killed job must not leave a worker behind."""
    import threading
    import time

    parent = os.getppid()

    def watch() -> None:
        while True:
            if os.getppid() != parent:
                os._exit(75)
            time.sleep(1.0)

    threading.Thread(target=watch, daemon=True).start()


def main(config_path: str) -> int:
    _exit_if_orphaned()
    cfg = json.loads(Path(config_path).read_text())
    if cfg.get("detect"):  # language ID on short samples; prints CFLANG|<index>|<code>
        d = cfg["detect"]
        samples = d["samples"] if "samples" in d else [[d["start"], d["end"]]]
        runner = make_runner(cfg["backend"], cfg["model"])
        for i, (a, b) in enumerate(samples):
            lang, _ = runner.run(read_slice(cfg["wav"], a, b), a, None, None)
            print(f"CFLANG|{i}|{lang}", flush=True)
        return 0
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = cfg["chunks"]
    runner = None
    done_secs = 0.0
    total_secs = sum(c["end"] - c["start"] for c in chunks) or 1.0
    for n, c in enumerate(chunks):
        target = out_dir / f"chunk_{c['index']:04d}.json"
        if target.exists():
            done_secs += c["end"] - c["start"]
            print(f"CFASR|{n + 1}|{len(chunks)}|{done_secs / total_secs:.4f}", flush=True)
            continue
        runner = runner or make_runner(cfg["backend"], cfg["model"])
        audio = read_slice(cfg["wav"], c["start"], c["end"])
        lang, words = runner.run(audio, c["start"], cfg.get("language"), cfg.get("prompt"))
        chunk = RawChunk(
            index=c["index"], start=c["start"], end=c["end"], language=lang, words=words
        )
        tmp = target.with_suffix(".partial")
        tmp.write_text(chunk.model_dump_json())
        os.replace(tmp, target)
        done_secs += c["end"] - c["start"]
        print(f"CFASR|{n + 1}|{len(chunks)}|{done_secs / total_secs:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--config") + 1]))
