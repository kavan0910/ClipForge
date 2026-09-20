"""NLE export through OpenTimelineIO: the cut list as a timeline that opens in Premiere/Resolve/Final Cut.

Video and audio tracks reference the ORIGINAL source file with one range per EDL segment (so the editor keeps
the cuts as separate, adjustable clips). Captions travel as markers on the video track plus the .srt sidecar;
FCPXML/EDL have no portable caption styling.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import opentimelineio as otio

from clipforge.captions.sidecars import to_srt  # noqa: F401  (re-exported for callers)
from clipforge.captions.timeline import Timeline
from clipforge.edl import EDL

FORMATS = {
    "otio": ("otio_json", ".otio"),
    "fcpxml": ("fcpx_xml", ".fcpxml"),
    "fcp7": ("fcp_xml", ".xml"),
    "edl": ("cmx_3600", ".edl"),
}


def exact_rate(fps: float) -> float:
    """Probed rates are rounded (23.976); NLE formats want the exact NTSC fractions."""
    for base in (24, 30, 48, 60, 120):
        if abs(fps - base * 1000 / 1001) < 0.01:
            return base * 1000 / 1001
    return float(round(fps, 3))


def build_timeline(
    name: str,
    master: Path,
    edl: EDL,
    fps: float,
    captions: Timeline | None = None,
    media_seconds: float | None = None,
) -> otio.schema.Timeline:
    rate = exact_rate(fps)
    ref = otio.schema.ExternalReference(target_url=master.resolve().as_uri())
    if media_seconds:  # NLEs and the FCPXML adapter need the full length of the source media
        ref.available_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, rate),
            otio.opentime.RationalTime(round(media_seconds * rate), rate),
        )
    tl = otio.schema.Timeline(name=name)
    tl.global_start_time = otio.opentime.RationalTime(0, rate)
    video, audio = (
        otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video),
        otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio),
    )
    for i, s in enumerate(edl.segments):
        rng = otio.opentime.TimeRange(
            otio.opentime.RationalTime(round(s.src_in * rate), rate),
            otio.opentime.RationalTime(round(s.length * rate), rate),
        )
        for track in (video, audio):
            c = otio.schema.Clip(
                name=f"{name} {i + 1}", media_reference=ref.clone(), source_range=rng
            )
            track.append(c)
    tl.tracks.append(video)
    tl.tracks.append(audio)
    if captions:
        for c in captions.chunks:
            text = " ".join(w.w for w in c.words)
            m = otio.schema.Marker(
                name=text,
                marked_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(round(c.start * rate), rate),
                    otio.opentime.RationalTime(max(round((c.end - c.start) * rate), 1), rate),
                ),
            )
            video.markers.append(m)
    return tl


def export_timeline(tl: otio.schema.Timeline, fmt: str, out: Path) -> Path:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown export format {fmt!r}. Choose from: {', '.join(FORMATS)}")
    adapter, ext = FORMATS[fmt]
    dest = out.with_suffix(ext)
    otio.adapters.write_to_file(tl, str(dest), adapter_name=adapter)
    return dest


def roundtrip_check(path: Path, fmt: str, edl: EDL, fps: float) -> dict:
    """Read the file back and compare segment count and duration with the EDL (within one frame)."""
    tl = otio.adapters.read_from_file(str(path), adapter_name=FORMATS[fmt][0])
    v = next((t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video), None)
    clips = list(v.find_clips()) if v else []
    dur = sum(c.duration().to_seconds() for c in clips)
    return {"clips": len(clips), "expected_clips": len(edl.segments), "seconds": round(dur, 3), "expected_seconds": round(edl.duration, 3),
            "ok": len(clips) == len(edl.segments) and abs(dur - edl.duration) <= 1.5 / fps}  # fmt: skip


def bundle(files: dict[str, list[Path]], dest: Path, as_zip: bool) -> Path:
    """Copy each clip's files under <slug>/ into a folder or a zip (bulk export)."""
    if as_zip:
        dest = dest.with_suffix(".zip")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for slug, paths in files.items():
                for p in paths:
                    if p.exists():
                        z.write(p, f"{slug}/{p.name}")
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    for slug, paths in files.items():
        (dest / slug).mkdir(exist_ok=True)
        for p in paths:
            if p.exists():
                shutil.copy2(p, dest / slug / p.name)
    return dest


def export_cut(
    name: str, master: Path, edl: EDL, fps: float, fmt: str, out: Path, captions: Timeline | None = None,
    media_seconds: float | None = None,
) -> tuple[Path, list[str]]:  # fmt: skip
    """Write one clip's cut list in `fmt`. Returns (path, warnings).

    otio-fcpx-xml-adapter 1.0.0 cannot write fractional (NTSC) rates; for FCPXML the timeline is then written at the
    nearest integer rate and the caller is warned (timing drifts by 0.1%). OTIO, FCP7 XML and EDL keep exact rates.
    """
    warnings: list[str] = []
    tl = build_timeline(name, master, edl, fps, captions, media_seconds)
    try:
        return export_timeline(tl, fmt, out), warnings
    except ValueError:
        if fmt != "fcpxml":
            raise
    rate = float(round(fps))
    warnings.append(
        f"FCPXML cannot store {fps:g} fps with the available adapter; it was written at {rate:g} fps (about 0.1% timing drift). "
        "Use FCP7 XML or OTIO for exact frame timing."
    )
    return export_timeline(
        build_timeline(name, master, edl, rate, captions, media_seconds), fmt, out
    ), warnings
