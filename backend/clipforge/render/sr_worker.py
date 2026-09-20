"""Worker for render/sr.py: `python -m clipforge.render.sr_worker`. Imports torch only (no OpenCV/PyAV)."""

from __future__ import annotations

import sys

import numpy as np


def build_model():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class SRVGGNetCompact(nn.Module):
        def __init__(self, num_feat: int = 64, num_conv: int = 32, upscale: int = 4) -> None:
            super().__init__()
            self.upscale = upscale
            self.body = nn.ModuleList(
                [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            )
            for _ in range(num_conv):
                self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
                self.body.append(nn.PReLU(num_parameters=num_feat))
            self.body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
            self.up = nn.PixelShuffle(upscale)

        def forward(self, x):
            out = x
            for m in self.body:
                out = m(out)
            return self.up(out) + F.interpolate(x, scale_factor=self.upscale, mode="nearest")

    from clipforge.render.sr import MODEL_PATH, available_device

    dev = available_device() or "cpu"
    model = SRVGGNetCompact()
    sd = torch.load(MODEL_PATH, map_location="cpu")
    model.load_state_dict(sd["params"] if "params" in sd else sd)
    model.eval().to(dev)
    if dev != "cpu":
        model.half()
    return model, dev


def main() -> int:
    import torch
    import torch.nn.functional as F

    from clipforge.render.sr import HEADER

    model, dev = build_model()
    dtype = torch.float16 if dev != "cpu" else torch.float32
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while True:
        head = inp.read(HEADER.size)
        if len(head) < HEADER.size:
            return 0
        w, h, ow, oh = HEADER.unpack(head)
        raw = inp.read(w * h * 3)
        if len(raw) < w * h * 3:
            return 0
        x = torch.from_numpy(np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)[..., ::-1].copy())
        x = x.permute(2, 0, 1)[None].to(dev, dtype) / 255.0
        with torch.no_grad():
            y = model(x)
            y = F.interpolate(
                y.float(), size=(oh, ow), mode="bicubic", antialias=True, align_corners=False
            )
        arr = (
            (y.clamp(0, 1) * 255.0 + 0.5)[0]
            .permute(1, 2, 0)
            .to(torch.uint8)
            .cpu()
            .numpy()[..., ::-1]
        )
        out.write(np.ascontiguousarray(arr).tobytes())
        out.flush()


if __name__ == "__main__":
    sys.exit(main())
