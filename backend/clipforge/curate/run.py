"""Two-stage curation: cheap high-recall scan, then a stronger model ranks and refines."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from clipforge.config import Settings
from clipforge.curate import postprocess as pp
from clipforge.curate.format import Chunk, chunk_lines, format_sentences, total_tokens
from clipforge.curate.schema import Candidate, Clip, CurateResult, ScanResult
from clipforge.errors import ClipforgeError
from clipforge.llm.client import Block, LLMClient
from clipforge.llm.meter import CostMeter
from clipforge.models import Source, Transcript
from clipforge.pipeline import rms_db_frames
from clipforge.signals import combine
from clipforge.signals.stage import Signals
from clipforge.store import Project

PROMPT_VERSION = "v3"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SINGLE_PASS_TOKENS = int(
    os.environ.get("CLIPFORGE_SINGLE_PASS_TOKENS", "30000")
)  # env: cheap live tests of the two-stage path
Mode = Literal["fresh", "more_like", "shorter", "different_topic"]

HAIKU = "claude-haiku-4-5-20251001"


def load_prompt(name: str, version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / version / f"{name}.md").read_text().strip()


def preset_models(preset: str, settings: Settings) -> tuple[str, str]:
    """(scan model, curate model) for a preset."""
    if preset == "economy":
        return settings.scan_model, settings.scan_model
    if preset == "max":
        return settings.max_scan_model, settings.max_curate_model
    return settings.scan_model, settings.curate_model  # balanced


@dataclass
class CurateParams:
    n_clips: int | None = None
    min_duration: float = 20.0
    max_duration: float = 90.0
    steering: str = ""
    preset: str = "balanced"
    language: str | None = None
    mode: Mode = "fresh"
    reference_clip_id: str | None = None
    weights: dict[str, float] = field(default_factory=lambda: {"llm": 0.75, "signal": 0.25})


@dataclass
class CurateOutcome:
    run_id: str
    clips: list[Clip]
    rejected: list[dict]
    usage: dict[str, float]
    models: dict[str, str]
    seconds: float
    path: Path


def auto_clip_count(duration: float) -> int:
    return max(3, min(12, round(duration / 60 / 6)))


def _video_context(source: Source, transcript: Transcript, sig: Signals | None) -> str:
    plat = source.platform
    parts = [f"Title: {plat.title or source.title}"]
    if plat.channel:
        parts.append(f"Channel: {plat.channel}")
    parts.append(f"Duration: {source.probe.duration / 60:.1f} minutes")
    speakers = sorted({s.speaker for s in transcript.sentences if s.speaker})
    if speakers:
        parts.append("Speakers: " + ", ".join(speakers))
    if plat.description:
        parts.append("Description: " + plat.description[:1500])
    if plat.chapters:
        parts.append(
            "Chapters: "
            + "; ".join(f"{c.get('title')} ({c.get('start_time')}s)" for c in plat.chapters[:30])
        )
    if sig and sig.available:
        parts.append("Signals available: " + ", ".join(sig.available))
    return "\n".join(parts)


def _instructions(p: CurateParams, n: int, existing: list[Clip], exemplar: Clip | None) -> str:
    lines = [
        f"Select up to {n} clips. Each must run {p.min_duration:.0f}-{p.max_duration:.0f} seconds "
        "(most great clips are 25-55). Return fewer rather than weak ones, but do not stop at one for a long video."
    ]
    if p.steering.strip():
        lines.append(
            f"Editor's steering (follow it, but never break the rules): {p.steering.strip()}"
        )
    if p.language:
        lines.append(f"Write titles, hooks, descriptions and hashtags in language: {p.language}.")
    if p.mode == "shorter":
        lines.append(
            "Prefer tighter cuts: favour clips of 20-40 seconds that get to the point fast."
        )
    if p.mode == "different_topic" and existing:
        lines.append(
            "Already chosen topics (pick DIFFERENT subjects):\n"
            + "\n".join(f"- {c.summary}" for c in existing)
        )
    if p.mode == "more_like" and exemplar:
        lines.append(
            f"Find moments similar in topic and style to this clip the editor liked:\n{exemplar.summary}\n{exemplar.transcript[:600]}"
        )
    return "\n".join(lines)


def _shortlist(
    cands: list[Candidate],
    sentences_by_id: dict[str, int],
    sentences,
    sig: Signals | None,
    limit: int,
) -> list[Candidate]:
    valid = [
        c
        for c in cands
        if c.start_sentence in sentences_by_id and c.end_sentence in sentences_by_id
    ]
    valid = [
        c for c in valid if sentences_by_id[c.start_sentence] <= sentences_by_id[c.end_sentence]
    ]
    valid.sort(key=lambda c: -c.strength)
    kept: list[Candidate] = []
    for c in valid:
        a = (
            sentences[sentences_by_id[c.start_sentence]].start,
            sentences[sentences_by_id[c.end_sentence]].end,
        )
        if all(
            pp.overlap_fraction(
                a,
                (
                    sentences[sentences_by_id[k.start_sentence]].start,
                    sentences[sentences_by_id[k.end_sentence]].end,
                ),
            )
            <= 0.5
            for k in kept
        ):
            kept.append(c)
    if sig:

        def score(c: Candidate) -> float:
            s = sentences[sentences_by_id[c.start_sentence]].start
            e = sentences[sentences_by_id[c.end_sentence]].end
            return 0.7 * c.strength / 100 + 0.3 * combine.span_score(sig.interest, s, e)

        kept.sort(key=score, reverse=True)
    return kept[:limit]


def curate(
    project: Project, source: Source, transcript: Transcript, sig: Signals | None,
    settings: Settings, params: CurateParams, on_progress=None, llm: LLMClient | None = None,
    meter: CostMeter | None = None, run_id: str | None = None, rms_db: list[float] | None = None,
) -> CurateOutcome:  # fmt: skip
    t0 = time.time()
    if not (settings.anthropic_api_key or llm):
        raise ClipforgeError(
            "No Anthropic API key is configured.", "Add ANTHROPIC_API_KEY to .env, then retry."
        )
    scan_model, curate_model = preset_models(params.preset, settings)
    run_id = run_id or time.strftime("run-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    run_dir = project.path("curation", "runs", run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    meter = meter or CostMeter(settings.max_job_cost_usd, run_dir / "usage.jsonl")
    if meter.path is None:
        meter.path = run_dir / "usage.jsonl"
    if llm is None:
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
        llm = LLMClient(key, meter, run_dir, PROMPT_VERSION)
    progress = on_progress or (lambda **_: None)

    sentences, words = transcript.sentences, transcript.words
    if not sentences:
        raise ClipforgeError(
            "The transcript has no speech, so there is nothing to curate.",
            "Try a source with talking.",
        )
    by_id = {s.id: k for k, s in enumerate(sentences)}

    def tag_fn(s) -> str:
        if not sig:
            return ""
        tags = combine.sentence_tags(
            s, sig.energy_db, sig.laughter or None, sig.applause or None,
            sig.heatmap or None, sig.chat or None, sig.energy_cuts,
        )  # fmt: skip
        return combine.format_tags(tags)

    lines = format_sentences(sentences, tag_fn)
    n = params.n_clips or auto_clip_count(source.probe.duration)
    existing = load_clips(project) if params.mode != "fresh" else []
    exemplar = next((c for c in existing if c.id == params.reference_clip_id), None)
    ctx = _video_context(source, transcript, sig)
    instructions = _instructions(params, n, existing, exemplar)
    curate_system = load_prompt("curate")
    tokens = total_tokens(lines)
    progress(stage="curate", pct=0.0, note=f"{tokens} transcript tokens")

    if tokens <= SINGLE_PASS_TOKENS:
        blocks = [
            Block(f"VIDEO CONTEXT\n{ctx}\n\nTRANSCRIPT\n" + "\n".join(lines), cache=True),
            Block(instructions),
        ]
        result = llm.structured(
            stage="curate_single", model=curate_model, system=curate_system, blocks=blocks,
            model_cls=CurateResult, tool_name="report_clips", max_tokens=8000,
        )  # fmt: skip
    else:
        scan_system = load_prompt("scan")
        chunks: list[Chunk] = chunk_lines(lines, 9000, 0.15)
        cands: list[Candidate] = []
        per_chunk = max(4, min(10, n))
        for k, ch in enumerate(chunks):
            text = "TRANSCRIPT EXCERPT\n" + "\n".join(lines[ch.lo : ch.hi + 1])
            res = llm.structured(
                stage="scan", model=scan_model, system=scan_system,
                blocks=[Block(text, cache=True), Block(f"Return up to {per_chunk} candidates.")],
                model_cls=ScanResult, tool_name="report_candidates", max_tokens=3000,
            )  # fmt: skip
            cands.extend(res.candidates)
            progress(
                stage="curate",
                pct=0.1 + 0.5 * (k + 1) / len(chunks),
                note=f"scanned {k + 1}/{len(chunks)}",
            )
        short = _shortlist(cands, by_id, sentences, sig, min(3 * n + 3, 30))
        if not short:
            return _finish(project, run_id, [], [], meter, scan_model, curate_model, t0, run_dir)
        excerpts = []
        for k, c in enumerate(short):
            i, j = by_id[c.start_sentence], by_id[c.end_sentence]
            lo, hi = i, j
            while lo > 0 and sentences[i].start - sentences[lo - 1].start <= 30:
                lo -= 1
            while hi + 1 < len(sentences) and sentences[hi + 1].end - sentences[j].end <= 30:
                hi += 1
            excerpts.append(
                f"=== CANDIDATE {k + 1}: {c.start_sentence}-{c.end_sentence} ({c.reason}) ===\n"
                + "\n".join(lines[lo : hi + 1])
            )
        blocks = [
            Block(
                f"VIDEO CONTEXT\n{ctx}\n\nSHORTLIST WITH SURROUNDING CONTEXT\n"
                + "\n\n".join(excerpts),
                cache=True,
            ),
            Block(instructions),
        ]
        result = llm.structured(
            stage="curate", model=curate_model, system=curate_system, blocks=blocks,
            model_cls=CurateResult, tool_name="report_clips", max_tokens=8000,
        )  # fmt: skip

    progress(stage="curate", pct=0.8, note="post-processing")
    rms = rms_db if rms_db is not None else rms_db_frames(Path(source.audio_path or ""))
    pparams = pp.Params(
        min_duration=params.min_duration, max_duration=params.max_duration,
        llm_weight=params.weights["llm"], signal_weight=params.weights["signal"],
    )  # fmt: skip
    resolved, rejected = [], []
    for prop in result.clips:
        r, why = pp.resolve(prop, sentences, words, rms, source.probe.duration, pparams)
        (
            resolved.append(r)
            if r
            else rejected.append(
                {"start": prop.start_sentence, "end": prop.end_sentence, "reason": why}
            )
        )
    if (
        transcript.skipped
    ):  # sections in other languages were not transcribed: no clip may cross them
        keep = []
        for r in resolved:
            hit = any(
                pp.overlap_fraction((r.start, r.end), (x["start"], x["end"])) > 0
                for x in transcript.skipped
            )
            if hit:
                rejected.append(
                    {
                        "start": r.start,
                        "end": r.end,
                        "reason": "overlaps a section in another language",
                    }
                )
            else:
                keep.append(r)
        resolved = keep
    for ex in existing:  # do not duplicate what is already in the project
        resolved = [
            r
            for r in resolved
            if pp.overlap_fraction((r.start, r.end), (ex.start, ex.end)) <= pparams.max_overlap
        ]
    resolved = pp.dedupe(resolved, pparams.max_overlap)
    sig_scores = [
        combine.span_score(sig.interest, r.start, r.end) if sig else 0.0 for r in resolved
    ]
    picked = pp.rank_and_select(resolved, sig_scores, n, source.probe.duration, pparams)
    base = max((int(c.id[1:]) for c in existing if c.id[1:].isdigit()), default=0)
    clips = [
        pp.build_clip(f"c{base + k + 1:03d}", r, sentences, words, rs, ss)
        for k, (r, rs, ss) in enumerate(picked)
    ]
    return _finish(
        project,
        run_id,
        clips,
        rejected,
        meter,
        scan_model,
        curate_model,
        t0,
        run_dir,
        params,
        existing,
    )


def _finish(
    project,
    run_id,
    clips,
    rejected,
    meter,
    scan_model,
    curate_model,
    t0,
    run_dir,
    params=None,
    existing=(),
):
    usage = meter.totals()
    models = {"scan": scan_model, "curate": curate_model}
    (run_dir / "clips.json").write_text(json.dumps([c.model_dump() for c in clips], indent=1))
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "prompt_version": PROMPT_VERSION, "models": models, "usage": usage,
        "rejected": rejected, "params": asdict(params) if params else {},
        "seconds": round(time.time() - t0, 2), "clip_ids": [c.id for c in clips],
    }, indent=1))  # fmt: skip
    if params is None or params.mode == "fresh":
        merged = clips
    else:
        merged = [*existing, *clips]
    save_clips(project, merged)
    return CurateOutcome(
        run_id, clips, rejected, usage, models, round(time.time() - t0, 2), run_dir
    )


def clips_file(project: Project) -> Path:
    return project.path("curation", "clips.json")


def load_clips(project: Project) -> list[Clip]:
    f = clips_file(project)
    return [Clip.model_validate(c) for c in json.loads(f.read_text())] if f.exists() else []


def save_clips(project: Project, clips: list[Clip]) -> None:
    f = clips_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".partial")
    tmp.write_text(json.dumps([c.model_dump() for c in clips], indent=1))
    os.replace(tmp, f)
