"""Curation eval harness: runs curate() on cached transcripts/signals and scores the result."""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clipforge.config import Settings
from clipforge.curate.run import PROMPT_VERSION, CurateParams, curate, preset_models
from clipforge.evalkit import metrics as m
from clipforge.evalkit.replay import RecordReplayClient
from clipforge.llm.client import LLMClient
from clipforge.llm.meter import CostMeter
from clipforge.models import Source, Transcript
from clipforge.signals.stage import Signals
from clipforge.store import Project

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


@dataclass
class VideoResult:
    video: str
    genre: str
    n_clips: int
    recall_at_3: float
    recall_at_5: float
    precision_at_5: float
    starts_on_sentence: float
    mid_word_cuts: int
    hard_reject_pass: float
    bad_hit_rate: float
    hook_supported: float
    usd: float


def _nan_to_none(x: Any) -> Any:
    return None if isinstance(x, float) and math.isnan(x) else x


def load_case(vid: str) -> tuple[Source, Transcript, Signals | None, list[float], dict[str, Any]]:
    d = EVAL_DIR / "cache" / vid
    source = Source.model_validate_json((d / "source.json").read_text())
    tr = Transcript.model_validate_json((d / "transcript.json").read_text())
    sig = (
        Signals.model_validate_json((d / "signals.json").read_text())
        if (d / "signals.json").exists()
        else None
    )
    rms = json.loads((d / "rms_db.json").read_text())
    labels = json.loads((EVAL_DIR / "labels" / f"{vid}.json").read_text())
    return source, tr, sig, rms, labels


def run_case(
    vid: str, settings: Settings, params: CurateParams, work: Path, real_client: Any | None = None,
    cache_tag: str = "default",
) -> tuple[VideoResult, list]:  # fmt: skip
    source, tr, sig, rms, labels = load_case(vid)
    project = Project.create(work, f"eval-{vid}"[:40].lower().replace("_", "-"))
    client = RecordReplayClient(EVAL_DIR / "llm_cache" / cache_tag, real_client)
    meter = CostMeter(settings.max_job_cost_usd, project.path("curation", "usage.jsonl"))
    llm = LLMClient(
        "k", meter, project.path("curation", "runs", "eval"), PROMPT_VERSION, client=client
    )  # type: ignore[arg-type]
    out = curate(
        project, source, tr, sig, settings, params, llm=llm, meter=meter, run_id="eval", rms_db=rms
    )
    pred = [m.Span(c.start, c.end) for c in out.clips]
    gold = [m.Span(g["start"], g["end"]) for g in labels["good"]]
    bad = [m.Span(b["start"], b["end"]) for b in labels.get("bad", [])]
    ok_bounds = [
        params.min_duration <= c.duration <= params.max_duration
        and c.scores.self_contained >= 40 and c.scores.payoff >= 40
        for c in out.clips
    ]  # fmt: skip
    res = VideoResult(
        video=vid, genre=labels.get("genre", ""), n_clips=len(out.clips),
        recall_at_3=m.recall_at_k(pred, gold, 3), recall_at_5=m.recall_at_k(pred, gold, 5),
        precision_at_5=m.precision_at_k(pred, gold, 5),
        starts_on_sentence=m.pct([m.starts_on_sentence_boundary(p, tr.sentences, tr.words) for p in pred]),
        mid_word_cuts=sum(m.cut_inside_word(t, tr.words) for p in pred for t in (p.start, p.end)),
        hard_reject_pass=m.pct(ok_bounds), bad_hit_rate=m.bad_hit_rate(pred, bad),
        hook_supported=m.pct([c.hook_check.passed for c in out.clips]), usd=out.usage["usd"],
    )  # fmt: skip
    return res, out.clips


def aggregate(results: list[VideoResult]) -> dict[str, Any]:
    def mean(name: str) -> float | None:
        vals = [getattr(r, name) for r in results if not math.isnan(getattr(r, name))]
        return round(statistics.fmean(vals), 4) if vals else None

    return {
        "videos": len(results), "recall_at_3": mean("recall_at_3"), "recall_at_5": mean("recall_at_5"),
        "precision_at_5": mean("precision_at_5"), "starts_on_sentence": mean("starts_on_sentence"),
        "mid_word_cuts": sum(r.mid_word_cuts for r in results), "hard_reject_pass": mean("hard_reject_pass"),
        "bad_hit_rate": mean("bad_hit_rate"), "hook_supported": mean("hook_supported"),
        "usd": round(sum(r.usd for r in results), 4),
    }  # fmt: skip


def run_suite(
    suite: str,
    settings: Settings,
    preset: str,
    work: Path,
    real_client: Any | None = None,
    only: list[str] | None = None,
) -> dict[str, Any]:
    cfg = json.loads((EVAL_DIR / "suites" / f"{suite}.json").read_text())
    params = CurateParams(preset=preset, n_clips=cfg.get("clips", 5))
    scan, cur = preset_models(preset, settings)
    results = []
    for vid in [v for v in cfg["videos"] if not only or v in only]:
        res, _ = run_case(
            vid, settings, params, work, real_client, cache_tag=f"{PROMPT_VERSION}-{preset}"
        )
        results.append(res)
    report = {
        "suite": suite, "prompt_version": PROMPT_VERSION, "preset": preset,
        "models": {"scan": scan, "curate": cur}, "date": time.strftime("%Y-%m-%d"),
        "aggregate": aggregate(results), "videos": [{k: _nan_to_none(v) for k, v in asdict(r).items()} for r in results],
    }  # fmt: skip
    out = EVAL_DIR / "results" / PROMPT_VERSION / f"{preset}-{cur}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report
