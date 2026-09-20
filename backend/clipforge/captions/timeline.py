"""Words -> caption chunks with line breaks, safe-zone fitting, hook and per-script fonts.

The timeline is renderer-neutral JSON: the ASS renderer and the Remotion renderer both draw
exactly these chunks, lines and timings, so preview and export cannot disagree about layout.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Literal

from PIL import ImageFont
from pydantic import BaseModel, Field

from clipforge.captions.spec import CaptionTemplate, Font, HookStyle
from clipforge.edl import RemappedWord

FRAME_W, FRAME_H = 1080, 1920
# Platform UI covers the bottom ~20% and a strip on the right edge (buttons); keep text inside.
SAFE = {"left": 0.06, "right": 0.14, "top": 0.08, "bottom": 0.20}
FONT_DIRS = [
    Path(__file__).resolve().parents[3] / "captions" / "fonts",
    Path.home() / "Clipforge" / "fonts",
]
SENTENCE_END = (".", "?", "!", "\u2026", "\u3002", "\uff1f", "\uff01")
PROFANITY = {"fuck", "fucking", "shit", "bullshit", "bitch", "asshole", "damn", "dick", "cunt"}
Script = Literal["latin", "cjk", "arabic"]
FALLBACKS: dict[str, Font] = {
    "cjk": Font(file="NotoSansSC.ttf", family="Noto Sans SC", weight=700),
    "arabic": Font(file="NotoSansArabic.ttf", family="Noto Sans Arabic", weight=700),
}
EMOJI_HINTS = {"money": "\U0001f4b0", "dollar": "\U0001f4b5", "love": "❤️", "fire": "\U0001f525", "idea": "\U0001f4a1",
               "win": "\U0001f3c6", "laugh": "\U0001f602", "brain": "\U0001f9e0", "warning": "⚠️", "secret": "\U0001f92b",
               "shocking": "\U0001f631", "growth": "\U0001f4c8", "time": "⏱️", "health": "\U0001fa7a", "planet": "\U0001f30d"}  # fmt: skip


class TWord(BaseModel):
    w: str  # display text (case/punctuation/profanity applied)
    start: float  # output time, seconds
    end: float
    emphasis: bool = False
    src_i: int = -1


class Chunk(BaseModel):
    id: int
    start: float
    end: float
    words: list[TWord]
    lines: list[list[int]]  # indices into `words`, at most 2 lines
    scale: float = 1.0  # < 1 when a long word forced the font size down
    script: str = "latin"
    font: Font | None = None  # per-script fallback font
    emoji: str | None = None
    box_w: float = 0.0  # measured text block size in frame fractions (for renderers/tests)
    box_h: float = 0.0


class HookBlock(BaseModel):
    text: str
    lines: list[str]
    start: float
    end: float
    scale: float = 1.0


class Timeline(BaseModel):
    template_id: str
    width: int = FRAME_W
    height: int = FRAME_H
    duration: float
    chunks: list[Chunk]
    hook: HookBlock | None = None
    safe: dict[str, float] = Field(default_factory=lambda: dict(SAFE))


def find_font(name: str) -> Path:
    for d in FONT_DIRS:
        if (d / name).exists():
            return d / name
    raise FileNotFoundError(
        f"Font {name} not found. Run scripts/fetch_fonts.py or add it to ~/Clipforge/fonts/"
    )


@lru_cache(maxsize=64)
def load_font(file: str, px: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(find_font(file)), px)
    try:
        vals: list[float] = []
        for a in f.get_variation_axes():
            name = a["name"].decode() if isinstance(a["name"], bytes) else str(a["name"])
            lo, hi, dflt = (
                float(a["minimum"] or 0),
                float(a["maximum"] or 0),
                float(a["default"] or 0),
            )
            vals.append(max(lo, min(hi, float(weight))) if "eight" in name else dflt)
        f.set_variation_by_axes(vals)
    except (OSError, AttributeError):
        pass  # static font
    return f


def detect_script(text: str) -> Script:
    cjk = arabic = 0
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF
            or 0x3400 <= o <= 0x4DBF
        ):
            cjk += 1
        elif (
            0x0600 <= o <= 0x06FF
            or 0x0750 <= o <= 0x077F
            or 0xFB50 <= o <= 0xFDFF
            or 0xFE70 <= o <= 0xFEFF
        ):
            arabic += 1
    n = sum(1 for c in text if not c.isspace()) or 1
    return "cjk" if cjk / n > 0.3 else "arabic" if arabic / n > 0.3 else "latin"


def display_text(word: str, t: CaptionTemplate) -> str:
    w = word
    if t.strip_punctuation:
        w = "".join(c for c in w if not unicodedata.category(c).startswith("P")) or w
    if t.mask_profanity and re.sub(r"\W", "", w.lower()) in PROFANITY:
        core = re.sub(r"\W", "", w)
        w = w.replace(core, core[0] + "*" * (len(core) - 1)) if core else w
    return w.upper() if t.case == "upper" else w.lower() if t.case == "lower" else w


@dataclass
class Metrics:
    """Measures text with the template's real font."""

    template: CaptionTemplate
    script: Script = "latin"

    def font(self, scale: float = 1.0, weight: int | None = None):
        f = FALLBACKS.get(self.script, self.template.font)
        return load_font(
            f.file, max(round(self.template.size * FRAME_W * scale), 6), weight or f.weight
        )

    def width(self, words: list[str], scale: float = 1.0) -> float:
        joiner = "" if self.script == "cjk" else " "
        text = joiner.join(words)
        f = self.font(scale)
        track = (
            self.template.tracking * self.template.size * FRAME_W * scale * max(len(text) - 1, 0)
        )
        # Arabic renders up to ~12% wider in libass than measured (synthetic bold, joining): keep a margin.
        return (f.getlength(text) + track) * (1.15 if self.script == "arabic" else 1.0)


def max_text_width(t: CaptionTemplate) -> float:
    pad = (t.box.pad_x * FRAME_W * 2) if t.box else 0.0
    stroke = t.stroke.width * FRAME_W * 2
    return FRAME_W * (1 - SAFE["left"] - SAFE["right"]) - pad - stroke


def break_lines(
    words: list[str], m: Metrics, limit: float, max_chars: int, scale: float
) -> list[list[int]] | None:
    """Greedy two-line fit. Returns index lists, or None if it needs a third line."""
    lines: list[list[int]] = [[]]
    for i, w in enumerate(words):
        trial = [words[j] for j in lines[-1]] + [w]
        too_wide = m.width(trial, scale) > limit
        too_long = (
            m.script == "latin"
            and sum(len(x) for x in trial) + len(trial) - 1 > max_chars
            and len(trial) > 1
        )
        if lines[-1] and (too_wide or too_long):
            if len(lines) == 2:
                return None
            lines.append([])
        lines[-1].append(i)
    return lines


def fit_chunk(
    words: list[str], t: CaptionTemplate, m: Metrics
) -> tuple[list[list[int]], float] | None:
    """Lines and font scale for `words`; shrinks the font (down to 55%) if one word is too wide."""
    limit = max_text_width(t)
    for scale in (1.0, 0.9, 0.8, 0.7, 0.62, 0.55):
        if all(m.width([w], scale) <= limit for w in words):
            lines = break_lines(words, m, limit, t.chunking.max_chars_per_line, scale)
            if lines is not None:
                return lines, scale
            if len(words) > 1:
                return None  # too much text for two lines: caller should start a new chunk
    if len(words) == 1:
        # A single very long word: shrink to exactly what fits (down to 30%) rather than overflow.
        need = limit / max(m.width(words, 1.0), 1.0) * 0.97
        return [[0]], max(min(need, 1.0), 0.3)
    return None


def build_chunks(
    words: list[RemappedWord], t: CaptionTemplate, emphasis: set[int] | None = None
) -> list[Chunk]:
    emphasis = emphasis or set()
    ck = t.chunking
    chunks: list[Chunk] = []
    cur: list[RemappedWord] = []
    target_secs = ck.max_words / ck.target_wps

    def flush() -> None:
        if not cur:
            return
        disp = [display_text(w.w, t) for w in cur]
        script = detect_script(" ".join(disp))
        m = Metrics(t, script)
        fit = fit_chunk(disp, t, m)
        assert fit is not None  # guaranteed by the grow-loop below
        lines, scale = fit
        tw = [
            TWord(w=d, start=w.out_start, end=w.out_end, emphasis=w.i in emphasis, src_i=w.i)
            for d, w in zip(disp, cur, strict=True)
        ]
        widths = [m.width([disp[i] for i in ln], scale) for ln in lines]
        chunks.append(Chunk(
            id=len(chunks), start=cur[0].out_start, end=cur[-1].out_end, words=tw, lines=lines, scale=scale, script=script,
            font=FALLBACKS.get(script), box_w=max(widths) / FRAME_W,
            box_h=len(lines) * t.size * t.line_height * scale * (FRAME_W / FRAME_H),
        ))  # fmt: skip
        cur.clear()

    for k, w in enumerate(words):
        if cur:
            gap = w.out_start - cur[-1].out_end
            prev_ends = cur[-1].w.rstrip().endswith(SENTENCE_END)
            trial = [display_text(x.w, t) for x in [*cur, w]]
            m = Metrics(t, detect_script(" ".join(trial)))
            overflow = fit_chunk(trial, t, m) is None
            enough = len(cur) >= ck.min_words
            long_enough = (cur[-1].out_end - cur[0].out_start) >= target_secs
            if (
                gap >= ck.pause_break
                or overflow
                or len(cur) >= ck.max_words
                or (enough and (prev_ends or long_enough))
            ):
                flush()
        cur.append(w)
        del k
    flush()
    # Show each chunk until the next one starts (or a short tail), never overlapping.
    for a, b in pairwise(chunks):
        a.end = min(max(a.end + 0.25, a.end), max(b.start - 0.02, a.end))
    if chunks:
        chunks[-1].end += 0.25
    return chunks


def suggest_emoji(chunks: list[Chunk], every: int = 4) -> None:
    """Cap: at most one emoji per `every` chunks, from a small keyword table (not LLM-suggested)."""
    last = -every
    for c in chunks:
        if c.id - last < every:
            continue
        text = " ".join(w.w.lower() for w in c.words)
        for key, e in EMOJI_HINTS.items():
            if key in text:
                c.emoji, last = e, c.id
                break


def build_hook(
    text: str, h: HookStyle, t: CaptionTemplate, duration: float, offset: float = 0.0
) -> HookBlock:
    font = h.font or t.font
    px_max = FRAME_W * h.max_width
    words = (text.upper() if h.case == "upper" else text).split()
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6):
        f = load_font(font.file, max(round(h.size * FRAME_W * scale), 6), font.weight)
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cur and f.getlength(trial) > px_max:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
        if len(lines) <= 3 and all(f.getlength(x) <= px_max for x in lines):
            return HookBlock(
                text=text,
                lines=lines,
                start=offset + 0.15,
                end=min(offset + 0.15 + h.seconds, duration),
                scale=scale,
            )
    return HookBlock(
        text=text,
        lines=[text],
        start=offset + 0.15,
        end=min(offset + 0.15 + h.seconds, duration),
        scale=0.6,
    )


def apply_lead(words: list[RemappedWord], lead: float) -> list[RemappedWord]:
    """Show words `lead` seconds early (captions read better slightly early than late; ADR-003)."""
    return [
        w.model_copy(
            update={
                "out_start": max(w.out_start - lead, 0.0),
                "out_end": max(w.out_end - lead, 0.02),
            }
        )
        for w in words
    ]


CAPTION_LEAD = 0.05


def build_timeline(
    words: list[RemappedWord], t: CaptionTemplate, duration: float, emphasis_src: set[int] | None = None,
    hook: str | None = None, hook_enabled: bool = True, lead: float = CAPTION_LEAD, hook_offset: float = 0.0,
) -> Timeline:  # fmt: skip
    chunks = build_chunks(apply_lead(words, lead), t, emphasis_src)
    if t.emoji:
        suggest_emoji(chunks)
    hk = build_hook(hook, t.hook, t, duration, hook_offset) if hook and hook_enabled else None
    return Timeline(template_id=t.id, duration=duration, chunks=chunks, hook=hk)
