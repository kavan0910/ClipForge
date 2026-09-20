"""Pipeline stages for Phase 1: ingest (acquire, probe, normalise) and transcribe."""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipforge import media
from clipforge.asr.chunking import merge_chunks, plan_chunks
from clipforge.asr.guards import GUARD_VERSION, apply_guards
from clipforge.asr.prompt import build_initial_prompt
from clipforge.asr.sentences import segment_sentences
from clipforge.asr.types import RawChunk
from clipforge.asr.vad import speech_regions
from clipforge.asr.worker import resolve_model
from clipforge.config import Settings
from clipforge.errors import ASRError, MediaError
from clipforge.models import Probe, Source, Transcript, Word
from clipforge.parallel import Background
from clipforge.procs import CancelToken, run_streaming
from clipforge.providers.base import Acquired, SourceProvider
from clipforge.store import Project, canonical_hash

STAGE_VERSIONS = {"acquire": 1, "probe": 1, "normalize": 1, "transcribe": 2}
GUARD_HOP = 0.1  # seconds per RMS frame for the silence guard


class Reporter:
    """Writes progress events (throttled) to the project's event log."""

    def __init__(self, project: Project, cancel: CancelToken | None = None) -> None:
        self.project, self.cancel = project, cancel
        self._last: dict[str, float] = {}

    def progress(self, stage: str, pct: float | None = None, **extra: object) -> None:
        now = time.monotonic()
        if pct is not None and pct < 1.0 and now - self._last.get(stage, 0) < 0.25:
            return
        self._last[stage] = now
        self.project.emit("progress", stage=stage, pct=pct, **extra)

    def stage_done(self, stage: str, seconds: float, cached: bool) -> None:
        self.project.emit("stage_done", stage=stage, seconds=seconds, cached=cached)


def default_asr_backend(settings: Settings) -> str:
    if settings.asr_backend != "auto":
        return settings.asr_backend
    import platform

    return (
        "mlx-whisper"
        if platform.system() == "Darwin" and platform.machine() == "arm64"
        else "faster-whisper"
    )


@dataclass
class IngestOptions:
    audio_track: int | None = None  # ffprobe stream index override


def ingest(
    project: Project,
    provider: SourceProvider,
    settings: Settings,
    reporter: Reporter,
    cancel: CancelToken,
    options: IngestOptions | None = None,
    with_proxy: bool = True,
) -> Source:
    """acquire -> probe/validate -> hash -> 16 kHz wav (+ 720p proxy unless `with_proxy` is False, in which
    case `ensure_proxy` builds it later, e.g. while transcription runs). Cached and resumable."""
    options = options or IngestOptions()
    acquired_file = project.path("source", "acquired.json")

    def do_acquire() -> list[str]:
        reporter.progress("acquire", 0.0)
        a = provider.acquire(
            project, cancel, lambda d: reporter.progress(d.pop("stage", "acquire"), **d)
        )
        acquired_file.write_text(
            json.dumps(
                {
                    "master": str(a.master),
                    "kind": a.kind,
                    "title": a.title,
                    "origin": a.origin,
                    "platform": a.platform.model_dump(),
                    "ytdlp_version": a.ytdlp_version,
                }
            )
        )
        return [str(a.master)]

    def acquired_ok() -> bool:
        return (
            acquired_file.exists()
            and Path(json.loads(acquired_file.read_text())["master"]).exists()
        )

    ident = provider.identity() if provider.kind != "upload" else "upload-consumed"
    rec, cached = project.run_stage(
        "acquire", STAGE_VERSIONS["acquire"], canonical_hash(ident, settings.max_source_height), {},
        do_acquire, acquired_ok,
    )  # fmt: skip
    reporter.stage_done("acquire", rec.seconds, cached)
    meta = json.loads(acquired_file.read_text())
    from clipforge.models import PlatformSignals

    acq = Acquired(
        master=Path(meta["master"]), kind=meta["kind"], title=meta["title"], origin=meta["origin"],
        platform=PlatformSignals(**meta["platform"]), ytdlp_version=meta["ytdlp_version"],
    )  # fmt: skip

    # -- probe and validate ----------------------------------------------
    media.check_free_space(project.root, 0)
    pr = media.probe(acq.master)
    content_hash = media.fast_hash(acq.master.resolve())
    track = (
        options.audio_track if options.audio_track is not None else media.default_audio_track(pr)
    )
    if track not in {a.index for a in pr.audio}:
        raise MediaError(
            f"Audio track {track} does not exist in this file.", "Pick a listed track."
        )
    filters = media.ffmpeg_filters()
    has_zimg = "zscale" in filters
    quality = media.quality_report(pr, track, has_zimg)
    media.check_free_space(project.root, int(pr.size * 0.25) + int(pr.duration * 32_000))
    reporter.project.emit("probe", duration=pr.duration, quality=quality.model_dump())

    # -- normalise: wav for ASR, proxy for analysis ------------------------
    wav = project.path("source", "audio_16k.wav")
    proxy = project.path("source", "proxy_720p.mp4")

    def do_wav() -> list[str]:
        media.make_wav(
            acq.master, wav, track, pr.duration, lambda p: reporter.progress("audio", p), cancel
        )
        return [str(wav)]

    rec, cached = project.run_stage(
        "normalize_audio", STAGE_VERSIONS["normalize"], content_hash, {"track": track},
        do_wav, wav.exists,
    )  # fmt: skip
    reporter.stage_done("normalize_audio", rec.seconds, cached)

    proxy_path: str | None = str(proxy) if (proxy.exists() and pr.video) else None
    if pr.video and with_proxy:
        proxy_path = _make_proxy_stage(
            project, acq.master, pr, content_hash, has_zimg, reporter, cancel
        )

    source = Source(
        id=project.id, kind=acq.kind, title=acq.title, content_hash=content_hash,  # type: ignore[arg-type]
        master_path=str(acq.master), probe=pr, selected_audio=track, quality=quality,
        platform=acq.platform, origin=acq.origin, ytdlp_version=acq.ytdlp_version,
        proxy_path=proxy_path, audio_path=str(wav),
    )  # fmt: skip
    project.path("source", "source.json").write_text(source.model_dump_json(indent=2))
    return source


def _make_proxy_stage(
    project: Project,
    master: Path,
    pr: Probe,
    content_hash: str,
    has_zimg: bool,
    reporter: Reporter,
    cancel: CancelToken,
) -> str:
    proxy = project.path("source", "proxy_720p.mp4")
    video = pr.video
    assert video is not None

    def do_proxy() -> list[str]:
        media.make_proxy(master, proxy, video, pr.duration, has_zimg,
                         lambda p: reporter.progress("proxy", p), cancel)  # fmt: skip
        return [str(proxy)]

    rec, cached = project.run_stage(
        "normalize_proxy", STAGE_VERSIONS["normalize"], content_hash, {"zimg": has_zimg}, do_proxy, proxy.exists,
    )  # fmt: skip
    reporter.stage_done("normalize_proxy", rec.seconds, cached)
    return str(proxy)


def ensure_proxy(
    project: Project, source: Source, reporter: Reporter, cancel: CancelToken
) -> Source:
    """Build the analysis proxy for a source ingested with `with_proxy=False` and save it into source.json."""
    if not source.probe.video:
        return source
    has_zimg = "zscale" in media.ffmpeg_filters()
    path = _make_proxy_stage(
        project,
        Path(source.master_path),
        source.probe,
        source.content_hash,
        has_zimg,
        reporter,
        cancel,
    )
    source = source.model_copy(update={"proxy_path": path})
    project.path("source", "source.json").write_text(source.model_dump_json(indent=2))
    return source


def load_source(project: Project) -> Source:
    return Source.model_validate_json(project.path("source", "source.json").read_text())


def rms_db_frames(wav_path: Path, hop: float = GUARD_HOP) -> list[float]:
    """Per-`hop` RMS level in dBFS, streamed so long files stay flat in memory."""
    out: list[float] = []
    with wave.open(str(wav_path), "rb") as w:
        n = int(w.getframerate() * hop)
        while True:
            raw = w.readframes(n)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            out.append(20 * np.log10(max(rms, 1e-7)))
    return out


def is_english(lang: str | None) -> bool:
    return lang is None or lang.lower().split("-")[0] == "en"


def pick_model(settings: Settings, lang: str | None) -> str:
    """Fast model for English; the accurate multilingual model for everything else (turbo is measurably worse there)."""
    other = settings.asr_model_non_english
    return settings.asr_model if is_english(lang) or other == "same" else other


def detect_language(
    project: Project, wav: Path, duration: float, backend: str, model: str, cancel: CancelToken
) -> str | None:
    """Language of the recording from a 30 s sample (cached per project). None if it cannot be told."""
    cache = project.path("transcript", "language.json")
    if cache.exists():
        return json.loads(cache.read_text()).get("language")
    start = max(min(duration * 0.3, duration - 30.0), 0.0)
    cfg = {"backend": backend, "model": model, "wav": str(wav),
           "detect": {"start": start, "end": min(start + 30.0, duration)}}  # fmt: skip
    cfg_path = project.path("transcript", "detect.json")
    cfg_path.write_text(json.dumps(cfg))
    found: list[str] = []
    code, _ = run_streaming(
        [sys.executable, "-m", "clipforge.asr.worker", "--config", str(cfg_path)],
        lambda line: found.append(line.split("|", 1)[1]) if line.startswith("CFLANG|") else None,
        cancel,
        {**os.environ, "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false"},
    )
    lang = found[0] if code == 0 and found else None
    cache.write_text(json.dumps({"language": lang}))
    return lang


def transcribe(
    project: Project,
    source: Source,
    settings: Settings,
    reporter: Reporter,
    cancel: CancelToken,
    language: str | None = None,
    brand_vocabulary: list[str] | None = None,
    diarize: bool = True,
) -> Transcript:
    """Chunked, resumable ASR with guards, sentence segmentation and optional diarization."""
    backend = default_asr_backend(settings)
    wav = Path(source.audio_path or "")
    model = settings.asr_model
    if settings.asr_model_non_english not in ("same", settings.asr_model):
        lang_hint = language or detect_language(
            project, wav, source.probe.duration, backend, model, cancel
        )
        model = pick_model(settings, lang_hint)
        if model != settings.asr_model:
            reporter.progress(
                "transcribe",
                0.0,
                note=f"Detected language '{lang_hint}': using {model} for accuracy",
            )
    prompt = build_initial_prompt(
        source.platform.title or source.title, source.platform.channel, source.platform.tags,
        source.platform.description, brand_vocabulary or [],
    )  # fmt: skip
    specs = plan_chunks(source.probe.duration)
    want_diar = bool(diarize and settings.hf_token)
    params = {
        "backend": backend, "model": model, "language": language, "prompt": prompt,
        "chunks": [(s.start, s.end) for s in specs], "diarize": want_diar,
        "guards": GUARD_VERSION,
    }  # fmt: skip
    out_file = project.path("transcript", "transcript.json")
    # Raw chunks are keyed by every parameter that changes ASR output (no stale reuse).
    asr_key = canonical_hash(backend, model, language, prompt, params["chunks"])[:12]
    chunk_dir = project.path("transcript", "chunks", asr_key)

    def run() -> list[str]:
        # Speaker diarization only needs the audio, so it runs beside the ASR worker (GPU) instead of after it.
        diar_job: Background[list] | None = None
        if want_diar:
            from clipforge.asr.diarize_pyannote import diarize_wav

            reporter.progress("diarize", 0.0)
            diar_job = Background(lambda: diarize_wav(wav, settings, cancel), "diarize")
        worker_cfg = {
            "backend": backend, "model": model, "wav": str(wav), "out_dir": str(chunk_dir),
            "chunks": [s.__dict__ for s in specs], "language": language, "prompt": prompt,
        }  # fmt: skip
        cfg_path = project.path("transcript", "worker.json")
        cfg_path.write_text(json.dumps(worker_cfg))
        tail_lines: list[str] = []

        def on_line(line: str) -> None:
            if line.startswith("CFASR|"):
                _, done, total, frac = line.split("|")
                reporter.progress("transcribe", float(frac), chunk=int(done), chunks=int(total))
            else:
                tail_lines.append(line)

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false"}
        code, tail = run_streaming(
            [sys.executable, "-m", "clipforge.asr.worker", "--config", str(cfg_path)],
            on_line,
            cancel,
            env,
        )
        if code != 0:
            raise ASRError(
                "Transcription failed.",
                "Run `clipforge doctor`; details: " + " | ".join(tail.splitlines()[-4:])[:300],
            )
        chunks = [
            RawChunk.model_validate_json(p.read_text())
            for p in sorted(chunk_dir.glob("chunk_*.json"))
        ]
        if len(chunks) != len(specs):
            raise ASRError("Transcription produced an incomplete result.", "Retry to resume it.")
        raw = merge_chunks(chunks)
        speech = speech_regions(wav)
        raw = apply_guards(raw, rms_db_frames(wav), GUARD_HOP, speech)
        words = [
            Word(i=i, w=r.w, start=round(r.start, 3), end=round(r.end, 3), prob=round(r.prob, 3))
            for i, r in enumerate(raw)
        ]
        diarized = False
        if diar_job is not None and words:
            from clipforge.asr.diarize import assign_speakers, label_speakers

            try:
                turns = diar_job.result()
            except ASRError as e:
                # Diarization is optional: keep single-speaker mode and tell the user why.
                project.emit("warning", message=e.message, action=e.action)
                turns = []
            except (
                Exception
            ) as e:  # e.g. torchcodec cannot find FFmpeg: never lose a finished transcript
                project.emit(
                    "warning",
                    message="Speaker identification was skipped: " + str(e).splitlines()[0][:160],
                    action="Transcription is unaffected; all speech is treated as one speaker. See the README for the FFmpeg fix.",
                )
                turns = []
            if turns:
                words = assign_speakers(words, label_speakers(turns))
                diarized = True
            reporter.progress("diarize", 1.0)
        sentences = segment_sentences(words)
        lang = chunks[0].language if chunks else (language or "en")
        t = Transcript(
            language=lang, asr_backend=backend, asr_model=resolve_model(backend, model),
            duration=source.probe.duration, words=words, sentences=sentences, diarized=diarized,
        )  # fmt: skip
        tmp = out_file.with_suffix(".partial")
        tmp.write_text(t.model_dump_json())
        os.replace(tmp, out_file)
        return [str(out_file)]

    rec, cached = project.run_stage(
        "transcribe",
        STAGE_VERSIONS["transcribe"],
        source.content_hash,
        params,
        run,
        out_file.exists,
    )
    reporter.stage_done("transcribe", rec.seconds, cached)
    return Transcript.model_validate_json(out_file.read_text())
