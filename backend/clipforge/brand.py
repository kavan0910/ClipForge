"""Brand kits: fonts, colours, logo watermark, intro/outro cards, default template, vocabulary.

Stored as JSON under ~/Clipforge/brand/<id>/kit.json with assets next to it. A kit adjusts caption
template tokens (text and accent colours), stamps a watermark on every frame of the clip body and
adds optional title cards before/after it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from clipforge.captions.timeline import FRAME_H, FRAME_W, find_font, load_font

Position = Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"]


class Logo(BaseModel):
    file: str  # PNG (with alpha) inside the kit folder
    position: Position = "top_right"
    opacity: float = 0.85
    width: float = 0.16  # fraction of frame width
    margin: float = 0.045


class Card(BaseModel):
    """A title card shown before (intro) or after (outro) the clip."""

    title: str
    subtitle: str = ""
    seconds: float = 1.6
    background: str = "#101418"
    text_color: str = "#FFFFFF"
    accent: str = "#FFB347"
    show_logo: bool = True


class BrandKit(BaseModel):
    id: str
    name: str
    text_color: str | None = None  # overrides the caption fill
    accent_color: str | None = None  # overrides the active-word colours
    font_file: str | None = None  # card font (defaults to the template font)
    logo: Logo | None = None
    intro: Card | None = None
    outro: Card | None = None
    default_template: str = "karaoke-pop"
    vocabulary: list[str] = Field(default_factory=list)  # fed to ASR as a glossary


def brand_dir() -> Path:
    return Path.home() / "Clipforge" / "brand"


def load_kit(kit_id: str, root: Path | None = None) -> BrandKit:
    f = (root or brand_dir()) / kit_id / "kit.json"
    if not f.exists():
        raise ValueError(f"Unknown brand kit {kit_id!r}. Create it with `clipforge brand create`.")
    return BrandKit.model_validate_json(f.read_text())


def save_kit(kit: BrandKit, root: Path | None = None) -> Path:
    d = (root or brand_dir()) / kit.id
    d.mkdir(parents=True, exist_ok=True)
    (d / "kit.json").write_text(kit.model_dump_json(indent=2))
    return d


def list_kits(root: Path | None = None) -> list[str]:
    r = root or brand_dir()
    return sorted(p.parent.name for p in r.glob("*/kit.json")) if r.exists() else []


def apply_kit(tpl, kit: BrandKit | None):
    """Return a template with the kit's colours applied (fill, active word, emphasis)."""
    if kit is None:
        return tpl
    upd: dict = {}
    if kit.text_color:
        upd["fill"] = kit.text_color
    if kit.accent_color:
        a = tpl.active.model_copy(
            update={
                "color": kit.accent_color,
                "underline_color": kit.accent_color,
                "pill_color": kit.accent_color,
            }
        )
        upd["active"] = a
    return tpl.model_copy(update=upd)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def load_logo(kit: BrandKit, kit_dir: Path) -> Image.Image | None:
    if not kit.logo:
        return None
    img = Image.open(kit_dir / kit.logo.file).convert("RGBA")
    w = round(FRAME_W * kit.logo.width)
    return img.resize((w, max(round(img.height * w / img.width), 1)), Image.Resampling.LANCZOS)


def watermark_overlay(kit: BrandKit, kit_dir: Path):
    """Callable(img_bgr, frame_index) -> img that alpha-blends the logo (None when the kit has no logo)."""
    logo = load_logo(kit, kit_dir)
    if logo is None or kit.logo is None:
        return None
    arr = np.asarray(logo, dtype=np.float32)
    alpha = arr[..., 3:4] / 255.0 * kit.logo.opacity
    rgb = arr[..., [2, 1, 0]]  # PIL RGBA -> BGR
    lh, lw = arr.shape[:2]
    m = round(FRAME_W * kit.logo.margin)
    pos = kit.logo.position
    x = (
        m
        if pos.endswith("left")
        else (FRAME_W - lw - m if pos.endswith("right") else (FRAME_W - lw) // 2)
    )
    y = (
        m + round(FRAME_H * 0.02)
        if pos.startswith("top")
        else (
            FRAME_H - lh - m - round(FRAME_H * 0.18)
            if pos.startswith("bottom")
            else (FRAME_H - lh) // 2
        )
    )

    def apply(img: np.ndarray, _frame: int) -> np.ndarray:
        region = img[y : y + lh, x : x + lw].astype(np.float32)
        img[y : y + lh, x : x + lw] = (region * (1 - alpha) + rgb * alpha).astype(np.uint8)
        return img

    return apply


def card_frames(
    card: Card, kit: BrandKit, kit_dir: Path, fps: float, font_file: str = "Montserrat.ttf"
) -> list[np.ndarray]:
    """BGR frames for a title card with a 0.25 s fade in/out (a few distinct frames are reused)."""
    n = max(round(card.seconds * fps), 2)
    base = Image.new("RGB", (FRAME_W, FRAME_H), _rgb(card.background))
    d = ImageDraw.Draw(base)
    ff = kit.font_file or font_file
    title_font = load_font(ff, round(FRAME_W * 0.085), 800)
    sub_font = load_font(ff, round(FRAME_W * 0.042), 500)
    y = FRAME_H * 0.42
    logo = load_logo(kit, kit_dir) if card.show_logo else None
    if logo:
        base.paste(logo, ((FRAME_W - logo.width) // 2, round(FRAME_H * 0.28)), logo)
    words, lines, cur = card.title.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if cur and title_font.getlength(trial) > FRAME_W * 0.8:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    lines.append(cur)
    for line in lines:
        d.text((FRAME_W / 2, y), line, font=title_font, fill=_rgb(card.text_color), anchor="mm")
        y += title_font.size * 1.2
    if card.subtitle:
        d.text(
            (FRAME_W / 2, y + 30), card.subtitle, font=sub_font, fill=_rgb(card.accent), anchor="mm"
        )
    d.rectangle([FRAME_W * 0.4, y + 90, FRAME_W * 0.6, y + 98], fill=_rgb(card.accent))
    arr = np.asarray(base)[..., ::-1]
    fade = round(0.25 * fps)
    frames = []
    for i in range(n):
        k = min(i / max(fade, 1), (n - 1 - i) / max(fade, 1), 1.0)
        frames.append((arr * max(k, 0.0)).astype(np.uint8))
    return frames


def font_exists(name: str) -> bool:
    try:
        find_font(name)
        return True
    except FileNotFoundError:
        return False
