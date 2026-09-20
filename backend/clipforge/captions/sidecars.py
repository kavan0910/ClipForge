"""SRT and VTT sidecars from a caption timeline (one cue per chunk)."""

from __future__ import annotations

from clipforge.captions.timeline import Timeline


def _cue_text(tl: Timeline, i: int) -> str:
    c = tl.chunks[i]
    joiner = "" if c.script == "cjk" else " "
    return "\n".join(joiner.join(c.words[k].w for k in line) for line in c.lines)


def _ts(t: float, sep: str) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def to_srt(tl: Timeline) -> str:
    out = []
    for i, c in enumerate(tl.chunks):
        out.append(f"{i + 1}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n{_cue_text(tl, i)}\n")
    return "\n".join(out)


def to_vtt(tl: Timeline) -> str:
    out = ["WEBVTT\n"]
    for i, c in enumerate(tl.chunks):
        out.append(f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{_cue_text(tl, i)}\n")
    return "\n".join(out)
