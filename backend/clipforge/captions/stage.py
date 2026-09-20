"""Caption stage for one clip: timeline, sidecars (.srt .vtt .ass) and the chosen renderer's output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from clipforge.brand import BrandKit, apply_kit
from clipforge.captions import onsets, remotion
from clipforge.captions.ass import write_ass
from clipforge.captions.sidecars import to_srt, to_vtt
from clipforge.captions.spec import CaptionTemplate, load_template
from clipforge.captions.timeline import Timeline, build_timeline
from clipforge.curate.schema import Clip
from clipforge.edl import EDL
from clipforge.models import Word
from clipforge.procs import CancelToken

FONTS_DIR = remotion.CAPTIONS_DIR / "fonts"


@dataclass
class CaptionOptions:
    template: str = "karaoke-pop"
    renderer: str = "auto"  # auto | remotion | ass
    hook: bool = True
    words: bool = True  # False: no word-by-word captions (the hook may still show)
    strip_punctuation: bool = False
    mask_profanity: bool = False
    overrides: dict = field(default_factory=dict)  # template token overrides (brand kit)


@dataclass
class CaptionResult:
    timeline: Timeline
    template: CaptionTemplate
    renderer: str
    ass_path: Path | None
    layers: list[tuple[Path, int]]
    sidecars: dict[str, Path]


def choose_renderer(pref: str) -> str:
    if pref == "auto":
        return "remotion" if remotion.node_available() else "ass"
    return pref


def prepare(
    d: Path, clip: Clip, edl: EDL, words: list[Word], opts: CaptionOptions, brand: BrandKit | None = None,
    cancel: CancelToken | None = None, fps: float = 30.0, offset: float = 0.0, tail: float = 0.0,
    wav: Path | None = None,
) -> CaptionResult:  # fmt: skip
    tpl = apply_kit(load_template(opts.template), brand)
    update = {"strip_punctuation": opts.strip_punctuation or tpl.strip_punctuation,
              "mask_profanity": opts.mask_profanity or tpl.mask_profanity, }  # fmt: skip
    tpl = tpl.model_copy(update=update)
    if (
        wav is not None and wav.exists()
    ):  # snap phrase starts after a pause to the acoustic onset (ADR-003)
        words = onsets.refine(words, onsets.rms_db_10ms(wav))
    # Shift into the final timeline: brand intro card first, outro card after.
    remapped = [
        w.model_copy(update={"out_start": w.out_start + offset, "out_end": w.out_end + offset})
        for w in (edl.remap_words(words) if opts.words else [])
    ]
    tl = build_timeline(
        remapped, tpl, offset + edl.duration + tail, set(clip.emphasis_word_indices),
        hook=clip.hook, hook_enabled=opts.hook, hook_offset=offset,
    )  # fmt: skip
    (d / "captions.json").write_text(tl.model_dump_json())
    (d / "captions_template.json").write_text(
        tpl.model_dump_json()
    )  # the in-app preview draws exactly this
    sidecars = {"srt": d / "out.srt", "vtt": d / "out.vtt", "ass": d / "out.ass"}
    sidecars["srt"].write_text(to_srt(tl), encoding="utf-8")
    sidecars["vtt"].write_text(to_vtt(tl), encoding="utf-8")
    write_ass(tl, tpl, sidecars["ass"])
    renderer = choose_renderer(opts.renderer)
    if renderer == "ass":
        return CaptionResult(tl, tpl, "ass", sidecars["ass"], [], sidecars)
    layers = []
    for name in ("captions", "hook"):
        layer = remotion.render_layer(tl, tpl, d, name, fps, cancel)
        if layer:
            layers.append((layer.concat, layer.y0))
    return CaptionResult(tl, tpl, "remotion", None, layers, sidecars)


def summary(res: CaptionResult) -> dict:
    t = res.timeline
    return {"template": res.template.id, "renderer": res.renderer, "chunks": len(t.chunks), "hook": bool(t.hook),
            "max_lines": max((len(c.lines) for c in t.chunks), default=0), "sidecars": {k: str(v.name) for k, v in res.sidecars.items()}}  # fmt: skip


def dump_summary(res: CaptionResult, path: Path) -> None:
    path.write_text(json.dumps(summary(res), indent=1))
