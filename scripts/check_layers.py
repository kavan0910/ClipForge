"""Check every exported caption still against the safe zone (alpha bounding boxes) and the two-line rule."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from clipforge.captions.timeline import FRAME_H, FRAME_W, SAFE
from clipforge.config import get_settings


def check(clip_dir: Path) -> dict:
    tl = json.loads((clip_dir / "captions.json").read_text())
    tpl = json.loads((clip_dir / "captions_template.json").read_text())
    res = {"clip": str(clip_dir.relative_to(get_settings().projects_dir)), "template": tpl["id"], "chunks": len(tl["chunks"]),
           "max_lines": max((len(c["lines"]) for c in tl["chunks"]), default=0)}  # fmt: skip
    for layer, y0 in (
        ("captions", max(round(tpl["anchor"]["y"] * FRAME_H - 0.15 * FRAME_H), 0)),
        ("hook", max(round(tpl["hook"]["y"] * FRAME_H - 0.13 * FRAME_H), 0)),
    ):
        pngs = sorted((clip_dir / f"layer_{layer}" / "png").glob("element-*.png"))
        if not pngs:
            continue
        x0 = y_lo = 10**9
        x1 = y_hi = -1
        for p in pngs:
            a = np.asarray(Image.open(p).convert("RGBA"))[..., 3] > 10
            ys, xs = np.where(a)
            if len(xs):
                x0, x1 = min(x0, int(xs.min())), max(x1, int(xs.max()))
                y_lo, y_hi = min(y_lo, y0 + int(ys.min())), max(y_hi, y0 + int(ys.max()))
        # Platform buttons sit on the right in the lower half; the top hook area only needs the symmetric margin.
        right = SAFE["left"] if layer == "hook" else SAFE["right"]
        inside = (
            x0 >= FRAME_W * SAFE["left"] - 4
            and x1 <= FRAME_W * (1 - right) + 4
            and y_lo >= FRAME_H * SAFE["top"]
            and y_hi <= FRAME_H * (1 - SAFE["bottom"])
        )
        res[layer] = {
            "stills": len(pngs),
            "bbox": [x0, y_lo, x1, y_hi],
            "inside_safe_zone": bool(inside),
        }
    return res


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(check(Path(arg).expanduser())))
