"""Signals stage: each expensive sub-signal is cached independently, then combined."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from clipforge.errors import ClipforgeError
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, rms_db_frames
from clipforge.procs import CancelToken, run_streaming
from clipforge.signals import audio, combine, platform, visual
from clipforge.store import Project, canonical_hash

SIGNALS_VERSION = 1


class Signals(BaseModel):
    """Per-second (1 Hz) feature arrays plus derived interest curve. Index = second."""

    duration: float
    energy_db: list[float]
    speech_rate: list[float]
    density: list[float]
    laughter: list[float] = Field(default_factory=list)
    applause: list[float] = Field(default_factory=list)
    scene_cuts: list[float] = Field(default_factory=list)
    cuts: list[float] = Field(default_factory=list)
    motion: list[float] = Field(default_factory=list)
    heatmap: list[float] = Field(default_factory=list)
    chat: list[float] = Field(default_factory=list)
    interest: list[float]
    energy_cuts: tuple[float, float]  # (low, high) dBFS thresholds: 25th/75th percentile
    available: list[str]


def _write(path: Path, data: object) -> None:
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def precompute_events(
    project: Project, source: Source, reporter: Reporter, cancel: CancelToken
) -> dict:
    """Laughter/applause (PANNs). Needs only the audio, so it can run while transcription does."""
    dur = source.probe.duration
    sdir = project.path("signals")
    ev_file = sdir / "events.json"

    def do_events() -> list[str]:
        laugh, clap = audio.event_probabilities(
            Path(source.master_path), source.selected_audio, dur, cancel,
            lambda p: reporter.progress("signals", p, part="events"),
        )  # fmt: skip
        _write(ev_file, {"laughter": laugh, "applause": clap})
        return [str(ev_file)]

    reporter.progress("signals", 0.0, part="events")
    rec, cached = project.run_stage(
        "signals_events", SIGNALS_VERSION, source.content_hash, {"model": "cnn14"}, do_events,
        ev_file.exists,
    )  # fmt: skip
    reporter.stage_done("signals_events", rec.seconds, cached)
    return json.loads(ev_file.read_text())


def precompute_visual(
    project: Project, source: Source, reporter: Reporter, cancel: CancelToken
) -> dict[str, list[float]]:
    """Shot changes and motion from the proxy (skipped for audio-only sources)."""
    dur = source.probe.duration
    vis_file = project.path("signals") / "visual.json"
    if not (source.proxy_path and source.has_video):
        return {}
    proxy = Path(source.proxy_path)

    def do_visual() -> list[str]:
        reporter.progress("signals", 0.4, part="visual")
        argv = [
            sys.executable,
            "-m",
            "clipforge.signals.visual",
            str(proxy),
            str(dur),
            str(vis_file),
        ]
        code, tail = run_streaming(argv, cancel=cancel)
        if code != 0:
            raise ClipforgeError("Visual analysis failed.", tail[-300:])
        return [str(vis_file)]

    rec, cached = project.run_stage(
        "signals_visual", SIGNALS_VERSION, source.content_hash, {}, do_visual, vis_file.exists
    )
    reporter.stage_done("signals_visual", rec.seconds, cached)
    return json.loads(vis_file.read_text())


def compute_signals(
    project: Project,
    source: Source,
    transcript: Transcript,
    reporter: Reporter,
    cancel: CancelToken,
    weights: dict[str, float] | None = None,
) -> Signals:
    dur = source.probe.duration
    sdir = project.path("signals")
    tkey = canonical_hash(source.content_hash, transcript.duration, len(transcript.words))
    ev = precompute_events(project, source, reporter, cancel)  # instant when already cached
    vis = precompute_visual(project, source, reporter, cancel)

    # Cheap features: recomputed whenever the transcript changes.
    wav = Path(source.audio_path or "")
    energy = audio.per_second_energy(rms_db_frames(wav), dur)
    rate, density = audio.speech_rate_and_density(transcript.words, dur)
    hm = (
        platform.heatmap_per_second(source.platform.heatmap, dur) if source.platform.heatmap else []
    )
    chat_file = project.path("source", "live_chat.json")
    chat = platform.chat_rate_per_second(chat_file, dur) if chat_file.exists() else []
    cuts_density = visual.cut_density(vis.get("scene_cuts", []), dur) if vis else []

    features = {
        "energy": energy, "speech_rate": rate, "density": density,
        "laughter": ev["laughter"], "applause": ev["applause"],
        "cuts": cuts_density, "motion": vis.get("motion", []), "heatmap": hm, "chat": chat,
    }  # fmt: skip
    n = int(np.ceil(dur))
    features = {k: v for k, v in features.items() if len(v) == n}
    interest = combine.interest_curve(features, weights)
    q = np.percentile(np.asarray(energy), [25, 75])
    sig = Signals(
        duration=dur, energy_db=energy, speech_rate=rate, density=density,
        laughter=ev["laughter"], applause=ev["applause"],
        scene_cuts=vis.get("scene_cuts", []), cuts=cuts_density, motion=vis.get("motion", []),
        heatmap=hm, chat=chat, interest=interest.tolist(),
        energy_cuts=(float(q[0]), float(q[1])), available=sorted(features),
    )  # fmt: skip
    out = sdir / "signals.json"
    _write(out, {"key": tkey, **sig.model_dump()})
    return sig


def load_signals(project: Project) -> Signals | None:
    f = project.path("signals", "signals.json")
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    d.pop("key", None)
    return Signals.model_validate(d)
