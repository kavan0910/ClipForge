"""ASS (libass) caption renderer: the fast draft path. Also used when Node/Chromium is unavailable.

libass cannot draw per-word pills or CSS glow, so those are approximated (thick rounded outline for
pills, blur for glow). The Remotion renderer is the faithful one; both consume the same Timeline.
"""

from __future__ import annotations

from pathlib import Path

from clipforge.captions.spec import CaptionTemplate, Font
from clipforge.captions.timeline import FRAME_H, FRAME_W, SAFE, Chunk, Timeline, load_font


def _bgr(hex_color: str, alpha: float = 1.0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = round((1 - alpha) * 255)
    return f"&H{a:02X}{b}{g}{r}".upper()


def _tc(hex_color: str) -> str:  # colour override without alpha
    h = hex_color.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _t(seconds: float) -> str:
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def ass_size(font: Font, em_px: float) -> float:
    """libass 'Fontsize' is the full line height (ascent+descent), not the em; convert so glyphs match
    the em size the timeline was measured with (PIL)."""
    px = max(round(em_px), 6)
    asc, desc = load_font(font.file, px, font.weight).getmetrics()
    return float(asc + desc) * em_px / px


def _style(
    name: str,
    t: CaptionTemplate,
    font: Font,
    size_px: float,
    fill: str,
    box: bool,
    outline_px: float,
    shadow_px: float,
) -> str:
    border = 3 if box else 1
    back = (
        _bgr(t.box.color, t.box.opacity)
        if (box and t.box)
        else _bgr(t.shadow.color, t.shadow.opacity)
    )
    outline_col = _bgr(t.box.color, t.box.opacity) if (box and t.box) else _bgr(t.stroke.color)
    bold = -1 if font.weight >= 600 else 0
    return (f"Style: {name},{font.family},{ass_size(font, size_px):.1f},{_bgr(fill)},{_bgr(fill)},{outline_col},{back},"
            f"{bold},{-1 if font.italic else 0},0,0,100,100,{t.tracking * size_px:.2f},0,{border},{outline_px:.1f},{shadow_px:.1f},5,0,0,0,1")  # fmt: skip


def _pos(y_frac: float) -> tuple[int, int]:
    x = FRAME_W * (SAFE["left"] + (1 - SAFE["left"] - SAFE["right"]) / 2)
    return round(x), round(FRAME_H * y_frac)


def _chunk_text(c: Chunk, t: CaptionTemplate, active: int | None, revealed: int | None) -> str:
    base = c.scale * 100
    """Text of one chunk with word `active` highlighted; words after `revealed` hidden (typewriter)."""
    joiner = "" if c.script == "cjk" else " "
    parts = []
    for line in c.lines:
        words = []
        for wi in line:
            w = c.words[wi]
            s = _esc(w.w)
            tags, reset = "", ""
            if w.emphasis and (t.emphasis.color or t.emphasis.scale != 1.0):
                if t.emphasis.color:
                    tags += f"\\c{_tc(t.emphasis.color)}"
                    reset += f"\\c{_tc(t.fill)}"
                if t.emphasis.scale != 1.0:
                    tags += (
                        f"\\fscx{base * t.emphasis.scale:.0f}\\fscy{base * t.emphasis.scale:.0f}"
                    )
                    reset += f"\\fscx{base:.0f}\\fscy{base:.0f}"
            if wi == active and t.active.mode != "none":
                a = t.active
                if a.mode == "color":
                    tags += f"\\c{_tc(a.color)}"
                    reset += f"\\c{_tc(t.fill)}"
                elif a.mode == "scale":
                    tags += f"\\c{_tc(a.color)}\\fscx{base * a.scale:.0f}\\fscy{base * a.scale:.0f}"
                    reset += f"\\c{_tc(t.fill)}\\fscx{base:.0f}\\fscy{base:.0f}"
                elif a.mode == "underline":
                    tags += f"\\u1\\c{_tc(a.underline_color)}"
                    reset += f"\\u0\\c{_tc(t.fill)}"
                elif a.mode == "pill":
                    pad = round(a.pill_radius * FRAME_W * 0.9)
                    tags += f"\\bord{pad}\\3c{_tc(a.pill_color)}\\c{_tc(a.color)}"
                    reset += f"\\bord{round(t.stroke.width * FRAME_W)}\\3c{_tc(t.stroke.color)}\\c{_tc(t.fill)}"
            if revealed is not None and wi > revealed:
                tags += "\\alpha&HFF&"
                reset += "\\alpha&H00&"
            words.append(f"{{{tags}}}{s}{{{reset}}}" if tags else s)
        parts.append(joiner.join(words))
    return "\\N".join(parts)


def _anim_tags(t: CaptionTemplate, first: bool, last: bool, base: float = 100.0) -> str:
    m = t.motion
    tags = ""
    if first and m.kind in ("fade", "typewriter") and m.ms:
        tags += f"\\fad({m.ms if m.kind == 'fade' else 60},0)"
    if first and m.kind == "pop":
        tags += f"\\fscx{base * 0.82:.0f}\\fscy{base * 0.82:.0f}\\t(0,{m.ms},\\fscx{base:.0f}\\fscy{base:.0f})"
    if first and m.kind == "slide_up":
        x, y = _pos(t.anchor.y)
        tags += f"\\move({x},{y + 40},{x},{y},0,{m.ms})"
    if last and m.out_kind == "fade" and m.out_ms:
        tags += f"\\fad(0,{m.out_ms})" if "\\fad(" not in tags else ""
    return tags


def render_ass(tl: Timeline, t: CaptionTemplate) -> str:
    size = t.size * FRAME_W
    outline = t.stroke.width * FRAME_W
    shadow = abs(t.shadow.dy) * FRAME_W if t.shadow.opacity > 0 else 0
    styles = [
        _style("Cap", t, t.font, size, t.fill, False, outline, shadow),
        _style("CapBox", t, t.font, size, t.fill, True, (t.box.pad_y * FRAME_W) if t.box else 0, 0),
    ]
    for key, f in {"cjk": "cjk", "arabic": "arabic"}.items():
        if any(c.script == key for c in tl.chunks) and any(
            c.font for c in tl.chunks if c.script == key
        ):
            font = next(c.font for c in tl.chunks if c.script == key and c.font)
            assert font is not None
            styles.append(_style(f"Cap_{f}", t, font, size, t.fill, False, outline, shadow))
            styles.append(
                _style(
                    f"CapBox_{f}",
                    t,
                    font,
                    size,
                    t.fill,
                    True,
                    (t.box.pad_y * FRAME_W) if t.box else 0,
                    0,
                )
            )
    h = t.hook
    hook_font = h.font or t.font
    styles.append(
        _style(
            "Hook",
            t,
            hook_font,
            h.size * FRAME_W,
            h.fill,
            h.box is not None,
            (h.box.pad_y * FRAME_W) if h.box else h.stroke.width * FRAME_W,
            0,
        )
    )
    if h.box:  # the hook has its own box colour
        styles[-1] = (
            styles[-1].replace(
                _bgr(t.box.color, t.box.opacity) if t.box else "",
                _bgr(h.box.color, h.box.opacity),
                1,
            )
            if t.box
            else styles[-1]
        )

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {FRAME_W}", f"PlayResY: {FRAME_H}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        *styles, "", "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]  # fmt: skip
    x, y = _pos(t.anchor.y)
    for c in tl.chunks:
        suffix = f"_{c.script}" if c.script in ("cjk", "arabic") and c.font else ""
        style = ("CapBox" if t.box else "Cap") + suffix
        n = len(c.words)
        for k in range(n):
            start = c.words[k].start if k else c.start
            end = c.words[k + 1].start if k + 1 < n else c.end
            if end <= start:
                continue
            scale = f"\\fscx{c.scale * 100:.0f}\\fscy{c.scale * 100:.0f}"
            active = k if t.active.mode != "none" else None
            revealed = k if t.motion.kind == "typewriter" else None
            text = _chunk_text(c, t, active, revealed)
            tag = (
                f"{{\\an5\\pos({x},{y}){scale}{_anim_tags(t, k == 0, k == n - 1, c.scale * 100)}}}"
            )
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},{style},,0,0,0,,{tag}{text}")
    if tl.hook:
        hx, hy = _pos(t.hook.y)
        text = "\\N".join(_esc(x) for x in tl.hook.lines)
        sc = (
            f"\\fscx{tl.hook.scale * 100:.0f}\\fscy{tl.hook.scale * 100:.0f}"
            if tl.hook.scale != 1.0
            else ""
        )
        lines.append(
            f"Dialogue: 1,{_t(tl.hook.start)},{_t(tl.hook.end)},Hook,,0,0,0,,{{\\an5\\pos({hx},{hy}){sc}\\fad(160,160)}}{text}"
        )
    return "\n".join(lines) + "\n"


def write_ass(tl: Timeline, t: CaptionTemplate, path: Path) -> Path:
    path.write_text(render_ass(tl, t), encoding="utf-8")
    return path
