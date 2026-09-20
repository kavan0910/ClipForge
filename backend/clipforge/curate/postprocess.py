"""Deterministic clip post-processing: boundary snapping, durations, dedupe, ranking, diversity.

Everything here is code, not LLM: it turns sentence-ID ranges into safe cut times and picks the
final set. Invariants (property-tested): a cut point never lies inside a word; pre-roll and
post-roll stay inside the pause around the clip.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from clipforge.curate.schema import Clip, ClipProposal, HookCheck
from clipforge.models import Sentence, Word

TERMINAL = (".", "?", "!", "\u2026", "\u3002", "\uff1f", "\uff01")


@dataclass(frozen=True)
class Params:
    min_duration: float = 20.0
    max_duration: float = 90.0
    target_min: float = 30.0
    target_max: float = 60.0
    preroll: float = 0.15  # 0.10-0.20 s of breathing room before the first word
    postroll: float = 0.35  # 0.25-0.45 s after the last word
    max_overlap: float = 0.30
    llm_weight: float = 0.75
    signal_weight: float = 0.25
    min_self_contained: int = 40
    min_payoff: int = 40
    diversity: float = 0.30


def _argmin_time(rms_db: Sequence[float], hop: float, lo: float, hi: float, prefer: float) -> float:
    """Time of the quietest frame in [lo, hi] (ties: closest to `prefer`), clamped inside."""
    a, b = max(int(lo / hop), 0), max(int(hi / hop), 0)
    frames = list(range(a, min(b + 1, len(rms_db))))
    if not frames:
        return min(max(prefer, lo), hi)
    best = min(frames, key=lambda k: (round(rms_db[k], 1), abs((k + 0.5) * hop - prefer)))
    return min(max((best + 0.5) * hop, lo), hi)


def snap_start(
    words: Sequence[Word],
    first: int,
    rms_db: Sequence[float],
    hop: float = 0.1,
    preroll: float = 0.15,
) -> float:
    """Cut time before `words[first]`: quietest point inside the preceding pause, with pre-roll."""
    first_start = words[first].start
    prev_end = words[first - 1].end if first > 0 else 0.0
    gap = max(first_start - prev_end, 0.0)
    lo = max(prev_end + min(0.02, gap / 4), first_start - 0.35)
    hi = first_start - min(max(preroll - 0.05, 0.10), gap / 2)
    if lo > hi:
        return prev_end + gap / 2 if first > 0 else max(first_start - gap / 2, 0.0)
    return _argmin_time(rms_db, hop, lo, hi, first_start - preroll)


def snap_end(
    words: Sequence[Word], last: int, rms_db: Sequence[float], duration: float,
    hop: float = 0.1, postroll: float = 0.35,
) -> float:  # fmt: skip
    """Cut time after `words[last]`: quietest point inside the following pause, with post-roll."""
    last_end = words[last].end
    next_start = words[last + 1].start if last + 1 < len(words) else max(duration, last_end)
    gap = max(next_start - last_end, 0.0)
    lo = last_end + min(0.25, gap / 2)
    hi = min(next_start - min(0.02, gap / 4), last_end + 0.45)
    if lo > hi:
        return last_end + gap / 2
    return _argmin_time(rms_db, hop, lo, hi, last_end + postroll)


def extend_to_sentence_boundaries(
    sentences: Sequence[Sentence], i: int, j: int, max_steps: int = 2, close: float = 0.6
) -> tuple[int, int]:
    """If the LLM range starts or ends mid-sentence (splitter broke on a pause), include the rest."""
    for _ in range(max_steps):
        if i > 0 and not sentences[i - 1].text.rstrip().endswith(TERMINAL):
            if sentences[i].start - sentences[i - 1].end < close:
                i -= 1
                continue
        break
    for _ in range(max_steps):
        if j + 1 < len(sentences) and not sentences[j].text.rstrip().endswith(TERMINAL):
            if sentences[j + 1].start - sentences[j].end < close:
                j += 1
                continue
        break
    return i, j


def overlap_fraction(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Overlap divided by the shorter clip's duration."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = min(a[1] - a[0], b[1] - b[0])
    return inter / shorter if shorter > 0 else 0.0


_STOP = {
    "about", "after", "again", "being", "could", "every", "first", "their", "there", "these", "thing",
    "think", "those", "under", "until", "which", "while", "would", "where", "other", "really", "because",
    "before", "actually", "never", "ever", "guess", "watch", "entire", "already", "still", "just",
}  # fmt: skip
# Words that signal attention-grabbing framing rather than a factual claim.


def _stem(w: str) -> str:
    """Crude prefix stem so 'killed'/'killing'/'killer' and 'violent'/'violence' compare equal."""
    return w[:5] if len(w) > 5 else w


def hook_check(hook: str, evidence_text: str, min_coverage: float = 0.6) -> HookCheck:
    """Truthfulness check: the hook's numbers and content words must occur in its evidence range.

    Deliberately conservative and stem-aware; a failure means "the editor should look", not "false".
    """
    ev_words = {
        _stem(t) for t in re.findall(r"[a-z0-9]+", re.sub(r"['\u2019]", "", evidence_text.lower()))
    }
    ev_words |= {t for t in re.findall(r"[a-z0-9]+", evidence_text.lower())}
    terms = [
        t for t in re.findall(r"[a-z0-9]+", re.sub(r"['\u2019]", "", hook.lower()))
        if t.isdigit() or (len(t) >= 5 and t not in _STOP)
    ]  # fmt: skip
    if not terms:
        return HookCheck(passed=True, coverage=1.0, missing=[])
    missing = [t for t in terms if _stem(t) not in ev_words and t not in ev_words]
    cov = 1 - len(missing) / len(terms)
    return HookCheck(passed=cov >= min_coverage, coverage=round(cov, 3), missing=missing)


def resolve_emphasis(words: Sequence[Word], sentence: Sentence, target: str) -> int | None:
    t = re.sub(r"\W+", "", target.lower())
    for w in words[sentence.word_lo : sentence.word_hi + 1]:
        if re.sub(r"\W+", "", w.w.lower()) == t:
            return w.i
    return None


_FILLER_OPEN = {
    "well", "so", "yeah", "yes", "and", "but", "okay", "ok", "um", "uh", "like", "right", "anyway", "now", "alright",
    "sure", "hmm", "mhm",
}  # fmt: skip


def weak_opener(sentence: Sentence) -> bool:
    """A lead-in that only warms up: a fragment of four words or fewer, or a short sentence led by a filler word."""
    words = re.findall(r"[\w']+", sentence.text.lower())
    if not words:
        return True
    if len(words) <= 4:
        return True
    return words[0] in _FILLER_OPEN and len(words) <= 9


def trim_lead_in(
    sentences: Sequence[Sentence], words: Sequence[Word], i: int, j: int, min_seconds: float, keep_from: int | None = None,
) -> int:  # fmt: skip
    """Drop up to two weak opening sentences (the 2-second rule), never below `min_seconds` of speech and never
    past `keep_from` (the sentence that carries the hook's evidence)."""
    limit = j if keep_from is None else keep_from
    for _ in range(2):
        if i >= j or i + 1 > limit:
            break
        if not weak_opener(sentences[i]):
            break
        remaining = words[sentences[j].word_hi].end - words[sentences[i + 1].word_lo].start
        if remaining < min_seconds:
            break
        i += 1
    return i


@dataclass
class Resolved:
    proposal: ClipProposal
    start: float
    end: float
    i: int  # first sentence index
    j: int  # last sentence index


def resolve(
    p: ClipProposal, sentences: Sequence[Sentence], words: Sequence[Word], rms_db: Sequence[float],
    duration: float, params: Params, hop: float = 0.1,
) -> tuple[Resolved | None, str]:  # fmt: skip
    """Map a proposal's sentence IDs to safe cut times. Returns (resolved, reject_reason)."""
    idx = {s.id: k for k, s in enumerate(sentences)}
    if p.start_sentence not in idx or p.end_sentence not in idx:
        return None, "unknown_sentence_id"
    i, j = idx[p.start_sentence], idx[p.end_sentence]
    if j < i:
        return None, "end_before_start"
    i, j = extend_to_sentence_boundaries(sentences, i, j)
    keep_from = idx.get(p.hook_evidence_start)
    i = trim_lead_in(sentences, words, i, j, params.min_duration + 2.0, keep_from)
    first, last = sentences[i].word_lo, sentences[j].word_hi
    start = snap_start(words, first, rms_db, hop, params.preroll)
    end = snap_end(words, last, rms_db, duration, hop, params.postroll)
    d = end - start
    if d > params.max_duration:
        return None, "too_long"
    if d < params.min_duration:
        return None, "too_short"
    if p.scores.self_contained < params.min_self_contained:
        return None, "needs_prior_context"
    if p.scores.payoff < params.min_payoff:
        return None, "ends_abruptly"
    return Resolved(p, start, end, i, j), ""


def dedupe(items: Sequence[Resolved], max_overlap: float) -> list[Resolved]:
    """Drop the lower-scored clip of any pair overlapping more than `max_overlap`."""
    kept: list[Resolved] = []
    for r in sorted(items, key=lambda r: -r.proposal.overall):
        if all(overlap_fraction((r.start, r.end), (k.start, k.end)) <= max_overlap for k in kept):
            kept.append(r)
    return kept


def _minmax(x: Sequence[float], min_range: float = 0.0) -> np.ndarray:
    """Min-max normalise within the video. `min_range` stops tiny score gaps being stretched to 0-1."""
    a = np.asarray(x, dtype=float)
    if a.size == 0:
        return a
    rng = a.max() - a.min()
    if rng == 0:
        return np.full(a.size, 0.5)
    return (a - a.min()) / max(rng, min_range)


def duration_factor(seconds: float) -> float:
    """Mild preference for the length short-form clips do best at: no penalty from 25 to 55 s, easing off outside it."""
    if seconds < 25:
        return 1.0 - 0.006 * (25 - seconds)
    if seconds > 55:
        return 1.0 - 0.005 * (seconds - 55)
    return 1.0


def rank_and_select(
    items: Sequence[Resolved],
    signal_scores: Sequence[float],
    n: int,
    video_duration: float,
    params: Params,
) -> list[tuple[Resolved, float, float]]:
    """Blend LLM and signal scores (normalised within the video) and pick `n` diverse clips.

    Returns (resolved, rank_score, signal_score) in final order. Diversity: each pick is
    penalised by its proximity in time to already chosen clips.
    """
    if not items:
        return []
    llm = _minmax([r.proposal.overall for r in items], min_range=20.0)
    sig = _minmax(signal_scores, min_range=0.2)
    blend = params.llm_weight * llm + params.signal_weight * sig
    blend = blend * np.array([duration_factor(r.end - r.start) for r in items])
    tau = max(video_duration / (2 * max(n, 1)), 30.0)
    chosen: list[int] = []
    remaining = set(range(len(items)))
    while remaining and len(chosen) < n:

        def value(k: int) -> float:
            if not chosen:
                return float(blend[k])
            near = max(math.exp(-abs(items[k].start - items[c].start) / tau) for c in chosen)
            return float(blend[k]) - params.diversity * near

        best = max(remaining, key=lambda k: (value(k), -items[k].start))
        chosen.append(best)
        remaining.discard(best)
    return [(items[k], float(blend[k]), float(np.asarray(signal_scores)[k])) for k in chosen]


def build_clip(
    clip_id: str, r: Resolved, sentences: Sequence[Sentence], words: Sequence[Word],
    rank_score: float, signal_score: float,
) -> Clip:  # fmt: skip
    p = r.proposal
    by_id = {s.id: s for s in sentences}
    emphasis = []
    for e in p.emphasis:
        s = by_id.get(e.sentence)
        if s and r.i <= sentences.index(s) <= r.j:
            w = resolve_emphasis(words, s, e.word)
            if w is not None:
                emphasis.append(w)
    ev_lo = by_id.get(p.hook_evidence_start)
    ev_hi = by_id.get(p.hook_evidence_end)
    lo = sentences.index(ev_lo) if ev_lo else r.i
    hi = sentences.index(ev_hi) if ev_hi else r.j
    evidence = " ".join(s.text for s in sentences[min(lo, hi) : max(lo, hi) + 1])
    check = hook_check(p.hook, evidence)
    flags: list[str] = list(p.risk_flags)
    if not check.passed and "unverified_claim" not in flags:
        flags.append("unverified_claim")
    return Clip(
        id=clip_id, start_sentence=sentences[r.i].id, end_sentence=sentences[r.j].id,
        start=round(r.start, 3), end=round(r.end, 3), duration=round(r.end - r.start, 3),
        title=p.title, hook=p.hook, summary=p.summary, why_it_works=p.why_it_works,
        scores=p.scores, overall=p.overall, signal_score=round(signal_score, 4),
        rank_score=round(rank_score, 4), emphasis_word_indices=sorted(set(emphasis)),
        description=p.description, hashtags=p.hashtags, risk_flags=flags, hook_check=check,
        transcript=" ".join(s.text for s in sentences[r.i : r.j + 1]),
    )  # fmt: skip
