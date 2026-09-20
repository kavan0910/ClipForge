"""Render one clip: cleanup EDL, face analysis, layout, camera, audio and a single encode."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from clipforge import brand as brandmod
from clipforge import edits as clipedits
from clipforge import media
from clipforge.brand import BrandKit
from clipforge.captions import stage as capstage
from clipforge.captions.stage import CaptionOptions
from clipforge.cleanup import Level
from clipforge.config import get_settings
from clipforge.curate.schema import Clip
from clipforge.edl import EDL
from clipforge.errors import ClipforgeError, MediaError
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, rms_db_frames
from clipforge.procs import CancelToken, run_streaming
from clipforge.reframe import camera as cam_mod
from clipforge.reframe import plan as rplan
from clipforge.reframe import punch
from clipforge.reframe.camera import Rect, crop_size
from clipforge.reframe.solve import OUT_H, OUT_W, Solved, solve
from clipforge.render import audio as raudio
from clipforge.render import measure as rmeasure
from clipforge.render import sr
from clipforge.render import video as rvideo
from clipforge.store import Project


@dataclass
class RenderResult:
    path: Path
    edl: EDL
    solved: Solved | None
    seconds: float
    measure: dict


def scale_solved(solved: Solved, sx: float, sy: float) -> Solved:
    """Analysis runs on the 720p proxy; renders crop the master: scale rectangles to master pixels."""
    for f in solved.frames:
        f.rects = [Rect(r.x * sx, r.y * sy, r.w * sx, r.h * sy) for r in f.rects]
    return solved


def clip_dir(project: Project, clip_id: str) -> Path:
    d = project.path("clips", clip_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_analysis(
    proxy: Path, out: Path, t0: float, t1: float, cancel: CancelToken, fps: float = 8.0
) -> dict:
    if not out.exists() or json.loads(out.read_text()).get("start") != t0:
        argv = [
            sys.executable,
            "-m",
            "clipforge.vision.analyze",
            str(proxy),
            str(out),
            str(t0),
            str(t1),
            str(fps),
        ]
        code, tail = run_streaming(argv, cancel=cancel)
        if code != 0:
            raise ClipforgeError("Face analysis failed.", tail[-300:])
    return json.loads(out.read_text())


def render_clip(
    project: Project, source: Source, transcript: Transcript, clip: Clip, level: Level = "light", fast: bool = False,
    debug: bool = False, reporter: Reporter | None = None, cancel: CancelToken | None = None, check_faces: bool = True,
    scene_cuts: list[float] | None = None, punch_in: float = 1.0, captions: CaptionOptions | None = None,
    brand: BrandKit | None = None, brand_root: Path | None = None, upscale: str | None = None,
) -> RenderResult:  # fmt: skip
    """Full Phase 3 render for one clip; writes clips/<id>/{edl,analysis,reframe,measure}.json + out.mp4."""
    t_start = time.time()
    cancel = cancel or CancelToken()
    report = reporter.progress if reporter else (lambda *a, **k: None)
    if not source.has_video or not source.probe.video or not source.proxy_path:
        from clipforge.render.audiogram import render_audiogram

        out = render_audiogram(
            project,
            source,
            transcript,
            clip,
            level,
            fast,
            reporter,
            cancel,
            captions,
            brand,
            brand_root,
        )
        d = clip_dir(project, clip.id)
        return RenderResult(out, EDL.model_validate_json((d / "edl.json").read_text()), None, time.time() - t_start,
                            json.loads((d / "measure.json").read_text()))  # fmt: skip
    video = source.probe.video
    d = clip_dir(project, clip.id)
    master = Path(source.master_path)
    wav = Path(source.audio_path or "")
    fps = video.fps
    tonemap_ok = "zscale" in media.ffmpeg_filters()
    media.check_free_space(d, int(clip.duration * 12_000_000 / 8) + 200_000_000)

    # 1. EDL: the clip's own cut plus filler/pause cleanup
    report("render", 0.02, note="planning cuts")
    rms = rms_db_frames(wav)
    # The editor's saved decisions (trims, text cuts, restores, layouts, template) win over defaults.
    edits = clipedits.load_edits(project, clip.id)
    if not clipedits.edits_path(project, clip.id).exists():
        edits = edits.model_copy(update={"cleanup": level})
    clip = clipedits.effective_clip(clip, edits)
    edl = clipedits.build_edl(clip, transcript.words, edits, rms, source.probe.duration)
    clip_start, clip_end = edl.segments[0].src_in, edl.segments[-1].src_out
    if edits.template and captions is not None:
        captions = replace(captions, template=edits.template)
    if edits.brand and brand is None:
        brand = brandmod.load_kit(edits.brand, brand_root)
    if captions is not None and not edits.hook_enabled:
        captions = replace(captions, hook=False)
    if captions is not None and not edits.captions_enabled:
        captions = replace(captions, words=False)
    cap_words = clipedits.apply_caption_text(transcript.words, edits)
    (d / "edl.json").write_text(edl.model_dump_json(indent=1))

    # 2. Face analysis on the proxy over the clip range
    report("render", 0.05, note="analysing faces")
    t0, t1 = max(clip_start - 1.0, 0.0), min(clip_end + 1.0, source.probe.duration)
    analysis = run_analysis(Path(source.proxy_path), d / "analysis.json", t0, t1, cancel)

    # 3. Layout plan and camera solve
    report("render", 0.15, note="planning camera")
    scene = rplan.scene_from_analysis(analysis)
    turns = rplan.turns_from_words(transcript.words, clip_start, clip_end)
    cw, _ = crop_size(scene.width, scene.height, OUT_W / OUT_H, 1.0)
    shots = rplan.shots_from_cuts(clip_start, clip_end, scene_cuts or [])
    fit = (edits.framing or get_settings().default_layout) == "fit"
    plan = rplan.build_plan(
        scene, shots, turns, cw, overrides=clipedits.layout_overrides(edits), fit=fit
    )
    if captions is not None and plan and all(seg.layout == "fit_blur" for seg in plan):
        captions = replace(
            captions, anchor_y=0.735
        )  # captions sit below the picture card, not over it
    solved = solve(
        scene, plan, edl, fps, punch_in=punch_in, max_zoom=cam_mod.max_zoom_for(video.height)
    )
    (d / "reframe.json").write_text(json.dumps({
        "plan": [{"t0": s.t0, "t1": s.t1, "layout": s.layout, "focus": s.focus, "cam_track": s.cam_track,
                  "tracks": s.tracks} for s in plan],
        "cut_frames": solved.cut_frames, "jerk": solved.jerk(),
        "x_frac": np.round(solved.x_frac, 5).tolist(),
    }))  # fmt: skip
    scale_solved(solved, video.width / analysis["width"], video.height / analysis["height"])
    if get_settings().render_punch_in == "auto" and any(
        f.layout == "fit_blur" for f in solved.frames
    ):
        emph = set(clip.emphasis_word_indices)
        beats = [w.out_start for w in edl.remap_words(transcript.words) if w.i in emph]
        takes = [(sg.out_in, sg.out_out) for sg in edl.segments]
        centres = [
            float(np.nanmedian(tr.cx)) / scene.width for tr in rplan.significant_tracks(scene)
        ]
        focus = float(np.median(centres)) if centres else 0.5
        curve = punch.zoom_curve(len(solved.frames), fps, beats, takes)
        punch.apply_fit_punch(solved.frames, curve, video.width, video.height, focus)

    # 4. Audio: EDL joins with fades, then two-pass loudness normalisation
    report("render", 0.2, note="mixing audio")
    pcm, norm = d / "audio_raw.wav", d / "audio_norm.wav"
    raudio.render_pcm(master, source.selected_audio, edl, pcm, cancel)
    raudio.normalise(pcm, norm, cancel)

    # 5. Brand cards, captions, watermark
    kit_dir = (brand_root or brandmod.brand_dir()) / brand.id if brand else None
    head = (
        brandmod.card_frames(brand.intro, brand, kit_dir, fps)
        if (brand and brand.intro and kit_dir)
        else []
    )
    tail = (
        brandmod.card_frames(brand.outro, brand, kit_dir, fps)
        if (brand and brand.outro and kit_dir)
        else []
    )
    offset, tail_s = len(head) / fps, len(tail) / fps
    final_audio = norm
    if head or tail:
        final_audio = d / "audio_final.wav"
        af = f"adelay={round(offset * 1000)}:all=1,apad=pad_dur={tail_s:.3f}"
        code, tail_log = run_streaming(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(norm), "-af", af,
                                        "-c:a", "pcm_f32le", str(final_audio)], cancel=cancel)  # fmt: skip
        if code != 0:
            raise MediaError("Could not pad the audio for the brand cards.", tail_log[-300:])
    cap_res = None
    if captions is not None:
        report("render", 0.22, note="captions")
        cap_res = capstage.prepare(
            d, clip, edl, cap_words, captions, brand, cancel, 30.0, offset, tail_s, wav
        )
        capstage.dump_summary(cap_res, d / "captions_summary.json")
    overlay = brandmod.watermark_overlay(brand, kit_dir) if (brand and kit_dir) else None

    # 6. One video encode
    out = d / "out.mp4"
    report("render", 0.25, note="rendering video")
    upscaler = None
    mode = upscale or get_settings().render_upscale
    factor = sr.enlargement(solved.frames)
    if mode != "off" and factor >= rvideo.AI_MIN_SCALE and sr.available_device():
        report(
            "render",
            0.24,
            note=f"AI upscaling (crops are enlarged {factor:.1f}x): this is slow but much sharper",
        )
        upscaler = sr.Upscaler()
    meas_upscale = "ai" if upscaler else "lanczos+sharpen"
    # Low-bitrate sources carry blocky compression noise that a second encode would only amplify: clean it lightly.
    bits = video.bit_rate or (Path(master).stat().st_size * 8 / max(source.probe.duration, 1.0))
    bpp = bits / max(video.width * video.height * fps, 1.0)
    denoise = get_settings().render_denoise == "auto" and bpp < 0.09
    try:
        rvideo.render_video(master, video, edl, solved, final_audio, out, fast, tonemap_ok,
                        lambda p: report("render", 0.25 + 0.7 * p), cancel, overlay=overlay,
                        ass_path=cap_res.ass_path if cap_res else None, fonts_dir=capstage.FONTS_DIR,
                        layers=cap_res.layers if cap_res else None, head=head, tail=tail,
                        preview=d / "base_preview.mp4", upscaler=upscaler, denoise=denoise)  # fmt: skip
    finally:
        if upscaler:
            upscaler.close()
    thumb_info = None
    if cap_res is not None or True:
        from clipforge.render import metadata as rmeta
        from clipforge.render import thumbnail as rthumb

        report("render", 0.96, note="thumbnail")
        best = rthumb.pick_frame(master, video, edl, solved, d, tonemap_ok)
        from clipforge.captions.spec import load_template

        tpl = cap_res.template if cap_res else load_template("karaoke-pop")
        rthumb.compose_title(
            Path(best["path"]), clip.hook, tpl, d / "thumb.jpg", best.get("face_box")
        )
        thumb_info = {k: best[k] for k in ("sharpness", "face", "eyes_open", "total", "candidates")}
    meas = rmeasure.measure_clip(out, solved, edl, check_faces, offset_frames=len(head))
    meas["render_seconds"] = round(time.time() - t_start, 1)
    meas["clip_seconds"] = round(edl.duration, 2)
    meas["upscale"] = {
        "method": meas_upscale,
        "enlargement": round(factor, 2),
        "denoise": denoise,
        "bits_per_pixel": round(bpp, 3),
    }
    meas["encoder"] = "h264_videotoolbox" if fast else "libx264 slow crf15"
    (d / "measure.json").write_text(json.dumps(meas, indent=1))
    rmeta.write_metadata(
        project,
        clip,
        edl,
        d,
        {
            "captions": capstage.summary(cap_res) if cap_res else None,
            "thumbnail": thumb_info,
            "brand": brand.id if brand else None,
            "measure": {k: meas[k] for k in ("loudness", "av_drift_frames", "jerk", "layout")},
        },
    )
    if debug:
        report("render", 0.97, note="debug render")
        render_debug(source, analysis, solved, edl, d / "debug.mp4", plan)
    report("render", 1.0)
    return RenderResult(out, edl, solved, time.time() - t_start, meas)


def render_debug(
    source: Source, analysis: dict, solved: Solved, edl: EDL, out: Path, plan: list
) -> None:
    """Source frames with face boxes, track ids, the crop rectangle and the layout label drawn on."""
    import subprocess

    import cv2

    video = source.probe.video
    assert video is not None
    scale = 960 / video.width
    dw, dh = 960, round(video.height * scale) // 2 * 2
    fps = solved.fps
    frames_by_t = analysis["frames"]
    times = np.array([f["t"] for f in frames_by_t])
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{dw}x{dh}",
            "-r", rvideo.fps_arg(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", str(out)]  # fmt: skip
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
    ax, ay = video.width / analysis["width"], video.height / analysis["height"]
    for seg in edl.segments:
        f0 = round(seg.out_in * fps)
        f1 = min(round(seg.out_out * fps), len(solved.frames))
        for i, frame in enumerate(
            rvideo.decode_segment(Path(source.master_path), video, seg.src_in, f1 - f0, fps, True)
        ):
            spec = solved.frames[f0 + i]
            img = cv2.resize(frame, (dw, dh))
            ts = seg.src_in + i / fps
            k = int(np.abs(times - ts).argmin())
            focus = spec.focus
            for fc in frames_by_t[k]["faces"]:
                x, y, w, h = (
                    int(v * s)
                    for v, s in (
                        (fc["x"] * ax, scale),
                        (fc["y"] * ay, scale),
                        (fc["w"] * ax, scale),
                        (fc["h"] * ay, scale),
                    )
                )
                col = (0, 255, 0) if fc["id"] == focus else (255, 160, 0)
                cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
                cv2.putText(
                    img,
                    f"#{fc['id']} m={fc['mouth']:.3f}",
                    (x, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    col,
                    1,
                )
            for r in spec.rects:
                if spec.layout != "fit_blur":
                    cv2.rectangle(
                        img,
                        (int(r.x * scale), int(r.y * scale)),
                        (int((r.x + r.w) * scale), int((r.y + r.h) * scale)),
                        (0, 255, 255),
                        2,
                    )
            cv2.putText(
                img,
                f"{spec.layout}{' CUT' if spec.cut else ''}  t={ts:.2f}",
                (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            assert proc.stdin is not None
            proc.stdin.write(img.tobytes())
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait()
