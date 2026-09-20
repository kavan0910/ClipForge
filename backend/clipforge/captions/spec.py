"""Caption template spec: JSON design tokens shared by the ASS renderer and the Remotion renderer.

One spec drives both, so what the in-app preview shows is what the export renders. Sizes are
fractions of the frame WIDTH (so templates work at any output size); positions are fractions of
the frame. The JSON Schema is exported to captions/spec/template.schema.json.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Case = Literal["none", "upper", "lower"]
ActiveMode = Literal["none", "color", "scale", "pill", "underline"]
AnimIn = Literal["none", "pop", "fade", "slide_up", "typewriter"]
Easing = Literal["linear", "ease_out", "ease_in_out", "back_out"]


class Font(BaseModel):
    file: str  # file name under captions/fonts or ~/Clipforge/fonts
    family: str  # family name as libass/Chromium see it
    weight: int = 700
    italic: bool = False


class Stroke(BaseModel):
    color: str = "#000000"
    width: float = 0.0  # fraction of frame width


class Shadow(BaseModel):
    color: str = "#000000"
    blur: float = 0.0  # fraction of frame width
    dx: float = 0.0
    dy: float = 0.004
    opacity: float = 0.6


class Box(BaseModel):
    color: str = "#000000"
    opacity: float = 0.55
    radius: float = 0.012  # fraction of frame width
    pad_x: float = 0.022
    pad_y: float = 0.010


class Active(BaseModel):
    """Treatment of the word being spoken."""

    mode: ActiveMode = "color"
    color: str = "#FFE14D"
    scale: float = 1.1
    pill_color: str = "#FF3B6B"
    pill_radius: float = 0.012
    underline_color: str = "#FFE14D"
    underline_height: float = 0.005


class Emphasis(BaseModel):
    """Words the LLM marked as keywords."""

    color: str | None = None
    scale: float = 1.0
    weight: int | None = None


class Motion(BaseModel):
    kind: AnimIn = "pop"
    ms: int = 180
    easing: Easing = "ease_out"
    out_kind: Literal["none", "fade"] = "fade"
    out_ms: int = 90


class Chunking(BaseModel):
    min_words: int = 1
    max_words: int = 4
    max_lines: int = Field(default=2, le=2)
    max_chars_per_line: int = 18
    pause_break: float = 0.45  # a pause this long always ends a chunk
    target_wps: float = 2.6  # chunks aim to hold about this many words per second of speech


class Anchor(BaseModel):
    y: float = (
        0.64  # vertical centre of the caption block, fraction of frame height (58-70% default)
    )
    align: Literal["center", "left"] = "center"


class HookStyle(BaseModel):
    """Title shown in the first seconds. Same design tokens, own placement and duration."""

    font: Font | None = None  # falls back to the template font
    size: float = 0.07
    case: Case = "none"
    fill: str = "#FFFFFF"
    stroke: Stroke = Stroke(color="#000000", width=0.004)
    box: Box | None = Box(color="#000000", opacity=0.6)
    y: float = 0.20
    seconds: float = 2.6
    max_width: float = 0.82


class CaptionTemplate(BaseModel):
    id: str
    label: str
    description: str = ""
    font: Font
    size: float = 0.075  # fraction of frame width
    case: Case = "none"
    tracking: float = 0.0  # em
    line_height: float = 1.12
    fill: str = "#FFFFFF"
    stroke: Stroke = Stroke()
    shadow: Shadow = Shadow()
    box: Box | None = None  # a backing box behind the whole chunk
    active: Active = Active()
    emphasis: Emphasis = Emphasis()
    motion: Motion = Motion()
    chunking: Chunking = Chunking()
    anchor: Anchor = Anchor()
    hook: HookStyle = HookStyle()
    emoji: bool = False  # show LLM-suggested emoji (capped)
    strip_punctuation: bool = False
    mask_profanity: bool = False


def export_schema() -> dict:
    return CaptionTemplate.model_json_schema()


def templates_dir():
    from pathlib import Path

    return Path(__file__).parent / "templates"


def load_template(template_id: str) -> CaptionTemplate:
    f = templates_dir() / f"{template_id}.json"
    if not f.exists():
        raise ValueError(
            f"Unknown caption template {template_id!r}. Available: {', '.join(list_templates())}"
        )
    return CaptionTemplate.model_validate_json(f.read_text())


def list_templates() -> list[str]:
    return sorted(p.stem for p in templates_dir().glob("*.json"))
