import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from clipforge.captions.ass import write_ass
from clipforge.captions.spec import CaptionTemplate
from clipforge.captions.timeline import Timeline, build_timeline
from clipforge.edl import RemappedWord

FONTS = Path(__file__).resolve().parents[2] / "captions" / "fonts"


def words_from(text: str, wps: float = 3.0, t0: float = 0.2) -> list[RemappedWord]:
    out, t = [], t0
    for i, tok in enumerate(text.split()):
        d = 0.9 / wps
        out.append(RemappedWord(i=i, w=tok, src_start=t, src_end=t + d, out_start=t, out_end=t + d))
        t += 1.0 / wps
    return out


def timeline_for(text: str, t: CaptionTemplate, **kw) -> Timeline:
    w = words_from(text)
    return build_timeline(w, t, duration=w[-1].out_end + 1.0, **kw)


def render_frame(
    tl: Timeline, t: CaptionTemplate, tmp: Path, at: float, bg: str = "black"
) -> np.ndarray:
    """One 1080x1920 RGB frame with captions burned in by libass (through ffmpeg)."""
    ass = write_ass(tl, t, tmp / "c.ass")
    png = tmp / f"f_{bg}.png"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s=1080x1920:r=25:d=6",
         "-vf", f"ass=filename={ass}:fontsdir={FONTS}", "-ss", str(at), "-frames:v", "1", str(png)],
        check=True,
    )  # fmt: skip
    return np.asarray(Image.open(png).convert("RGB"))


def bbox_diff(
    frame: np.ndarray, bg: tuple[int, int, int], thr: int = 12
) -> tuple[int, int, int, int] | None:
    d = np.abs(frame.astype(int) - np.array(bg)).max(axis=2) > thr
    ys, xs = np.where(d)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _lum(rgb) -> float:
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2])


def wcag(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readability(frame: np.ndarray, bg: tuple[int, int, int], fill_hex: str) -> float:
    """WCAG contrast between the caption's fill colour and the ring of pixels around it.

    The ring is what the eye compares the letters against: stroke, box, glow or, failing those,
    the raw background. >= 3:1 is the WCAG threshold for large text.
    """
    from scipy.ndimage import binary_dilation

    fill = tuple(int(fill_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    dist = np.abs(frame.astype(int) - np.array(fill)).max(axis=2)
    fillmask = np.asarray(dist < 30, dtype=bool)
    if fillmask.sum() < 50:
        return 0.0
    ring = np.asarray(binary_dilation(fillmask, iterations=3), dtype=bool) & ~fillmask
    ring_mean = frame[ring].mean(axis=0)
    return wcag(fill, ring_mean)
