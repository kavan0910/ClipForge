"""Audiogram layout for audio-only sources: a designed 1080x1920 canvas with an audio-reactive spectrum.

Same pipeline as video clips (EDL cuts, two-pass loudness, designer captions, one encode); only the picture
source differs. Frames are generated in Python and piped into the single encode.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from clipforge import brand as brandmod
from clipforge import edits as clipedits
from clipforge.captions import stage as capstage
from clipforge.captions.spec import load_template
from clipforge.captions.stage import CaptionOptions
from clipforge.cleanup import Level
from clipforge.curate.schema import Clip
from clipforge.errors import MediaError
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, rms_db_frames
from clipforge.procs import Cancelled, CancelToken, kill_tree
from clipforge.reframe.solve import OUT_H, OUT_W
from clipforge.render import audio as raudio
from clipforge.render import metadata as rmeta
from clipforge.render import thumbnail as rthumb
from clipforge.render import video as rvideo
from clipforge.store import Project

FPS = 30.0
BARS = 44
SR = 48000


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def spectrum_frames(pcm: np.ndarray, frames: int, bars: int = BARS, sr: int = SR) -> np.ndarray:
    """(frames, bars) in 0..1: log-spaced band energy per video frame, smoothed with fast attack, slow decay."""
    win = 2048
    edges = np.geomspace(80, 8000, bars + 1)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    idx = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in pairwise(edges)]
    hann = np.hanning(win)
    out = np.zeros((frames, bars), dtype=np.float32)
    for f in range(frames):
        centre = int((f + 0.5) / FPS * sr)
        chunk = pcm[max(centre - win // 2, 0) : centre + win // 2]
        if len(chunk) < win:
            chunk = np.pad(chunk, (0, win - len(chunk)))
        mag = np.abs(np.fft.rfft(chunk * hann)) / (win / 4)  # ~amplitude of a sine in that bin
        out[f] = [mag[i].mean() if len(i) else 0.0 for i in idx]
    db = 20 * np.log10(out + 1e-6)
    lvl = np.clip((db + 75) / 50, 0, 1)  # -75 dBFS floor .. -25 dBFS ceiling per band
    sm = np.zeros_like(lvl)
    for f in range(frames):
        prev = sm[f - 1] if f else np.zeros(bars, dtype=np.float32)
        sm[f] = np.where(lvl[f] > prev, prev + (lvl[f] - prev) * 0.7, prev + (lvl[f] - prev) * 0.25)
    return sm


def background(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0, 1, OUT_H, dtype=np.float32)[:, None, None]
    a, b = np.array(top, np.float32), np.array(bottom, np.float32)
    grad = (a * (1 - t) + b * t).astype(np.uint8)
    return np.repeat(grad, OUT_W, axis=1)[..., ::-1].copy()  # RGB -> BGR


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in sorted((capstage.FONTS_DIR).glob("*Bold*.ttf")) + sorted(
        capstage.FONTS_DIR.glob("*.ttf")
    ):
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


def title_layer(title: str, colour: tuple[int, int, int]) -> np.ndarray:
    """Static BGR overlay + alpha (H, W, 4) with the wrapped title near the top safe zone."""
    img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(64)
    words, lines, cur = title.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=font) > OUT_W * 0.78 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    lines.append(cur)
    y = int(OUT_H * 0.14)
    for ln in lines[:3]:
        w = d.textlength(ln, font=font)
        d.text(((OUT_W - w) / 2, y), ln, font=font, fill=(*colour, 255))
        y += 80
    return np.asarray(img)


def draw_frame(
    base: np.ndarray, title: np.ndarray, levels: np.ndarray, colour: tuple[int, int, int]
) -> np.ndarray:
    img = base.copy()
    a = title[..., 3:4].astype(np.float32) / 255
    img[:] = (title[..., 2::-1] * a + img * (1 - a)).astype(np.uint8)
    slot = OUT_W * 0.84 / len(levels)
    x0 = OUT_W * 0.08
    cy = int(OUT_H * 0.44)
    bgr = colour[::-1]
    for k, v in enumerate(levels):
        h = int(24 + v * 420)
        x = int(x0 + k * slot)
        img[cy - h // 2 : cy + h // 2, x : x + int(slot * 0.62)] = bgr
    return img


def render_audiogram(
    project: Project, source: Source, transcript: Transcript, clip: Clip, level: Level = "light",
    fast: bool = False, reporter: Reporter | None = None, cancel: CancelToken | None = None,
    captions: CaptionOptions | None = None, brand: brandmod.BrandKit | None = None, brand_root: Path | None = None,
) -> Path:  # fmt: skip
    t_start = time.time()
    cancel = cancel or CancelToken()
    report = reporter.progress if reporter else (lambda *a, **k: None)
    d = project.path("clips", clip.id)
    d.mkdir(parents=True, exist_ok=True)
    wav = Path(source.audio_path or "")
    report("render", 0.02, note="planning cuts")
    edits = clipedits.load_edits(project, clip.id)
    if not clipedits.edits_path(project, clip.id).exists():
        edits = edits.model_copy(update={"cleanup": level})
    clip = clipedits.effective_clip(clip, edits)
    edl = clipedits.build_edl(
        clip, transcript.words, edits, rms_db_frames(wav), source.probe.duration
    )
    (d / "edl.json").write_text(edl.model_dump_json(indent=1))
    if edits.template and captions is not None:
        captions = replace(captions, template=edits.template)
    if edits.brand and brand is None:
        brand = brandmod.load_kit(edits.brand, brand_root)
    if captions is not None and not edits.hook_enabled:
        captions = replace(captions, hook=False)
    if captions is not None and not edits.captions_enabled:
        captions = replace(captions, words=False)
    cap_words = clipedits.apply_caption_text(transcript.words, edits)

    report("render", 0.1, note="mixing audio")
    pcm, norm = d / "audio_raw.wav", d / "audio_norm.wav"
    raudio.render_pcm(Path(source.master_path), source.selected_audio, edl, pcm, cancel)
    loud = raudio.normalise(pcm, norm, cancel)
    mono = _read_mono(norm)

    tpl_id = captions.template if captions else "karaoke-pop"
    accent = _hex(
        (brand.accent_color if brand and brand.accent_color else None)
        or load_template(tpl_id).active.color
    )
    frames = round(edl.duration * FPS)
    levels = spectrum_frames(mono, frames)
    base = background(_hex("#0B1020"), _hex("#1B1F3B"))
    title = title_layer(clip.title, (255, 255, 255))

    cap_res = None
    if captions is not None:
        report("render", 0.2, note="captions")
        cap_res = capstage.prepare(
            d, clip, edl, cap_words, captions, brand, cancel, FPS, 0.0, 0.0, wav
        )
        capstage.dump_summary(cap_res, d / "captions_summary.json")

    out = d / "out.mp4"
    proc = subprocess.Popen(
        rvideo.encode_argv(out.with_suffix(".partial.mp4"), norm, FPS, edl.duration, fast,
                           ass_path=cap_res.ass_path if cap_res else None, fonts_dir=capstage.FONTS_DIR,
                           layers=cap_res.layers if cap_res else None),
        stdin=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)  # fmt: skip
    try:
        assert proc.stdin is not None
        for f in range(frames):
            if cancel.cancelled:
                raise Cancelled
            img = draw_frame(base, title, levels[f], accent)
            if f == min(30, frames - 1):
                Image.fromarray(img[..., ::-1]).save(d / "thumb_base.png")
            proc.stdin.write(img.tobytes())
            if f % 30 == 0:
                report("render", 0.25 + 0.7 * f / frames)
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise MediaError("The video encode failed.", err[-300:])
    except BrokenPipeError:
        raise MediaError("The encoder stopped early.", "") from None
    finally:
        if proc.poll() is None:
            kill_tree(proc, 2.0)
    out.with_suffix(".partial.mp4").replace(out)

    tpl = cap_res.template if cap_res else load_template(tpl_id)
    rthumb.compose_title(d / "thumb_base.png", clip.hook, tpl, d / "thumb.jpg", None)
    meas = {"loudness": loud, "clip_seconds": round(edl.duration, 2), "render_seconds": round(time.time() - t_start, 1),
            "layout": "audiogram"}  # fmt: skip
    (d / "measure.json").write_text(json.dumps(meas, indent=1))
    rmeta.write_metadata(project, clip, edl, d, {"captions": capstage.summary(cap_res) if cap_res else None,
                                                  "thumbnail": None, "brand": brand.id if brand else None, "measure": meas})  # fmt: skip
    report("render", 1.0)
    return out


def _read_mono(wav: Path) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                          capture_output=True, check=False)  # fmt: skip
    if proc.returncode != 0:
        raise MediaError(
            "Could not read the mixed audio.", proc.stderr.decode(errors="replace")[-300:]
        )
    return np.frombuffer(proc.stdout, dtype=np.float32)
