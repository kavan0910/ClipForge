# ADR-004: render path (single encode with time-varying crops)

- Status: accepted
- Date: 2026-09-19

## Problem
A clip needs per-frame crops that follow a smoothed camera path and change layout (single, stacked, screen+cam, blurred
fit), remapped through the EDL, and it must be compressed exactly once.

## Options
- **A. Pure ffmpeg filtergraph** (`crop` + `scale=lanczos`): fastest, but time-varying crop expressions and multi-region
  layouts are impractical to express for arbitrary smoothed paths.
- **B. Frame pipe**: ffmpeg decodes each kept segment (frame-accurate seek, VFR conformed, HDR tone-mapped, rotation
  applied) -> Python composes each output frame (Lanczos `cv2.resize` of a whole-pixel crop; stacked/screen/blur layouts
  are plain numpy) -> raw frames stream into ONE ffmpeg process that also muxes the processed audio.

## Benchmark (`scripts/bench_render.py`, real 20 s clip, 1920x1080 -> 1080x1920, static crop, M1 8 GB)
| Path | Wall time | Speed | Size | VMAF vs lossless of the same path |
|---|---|---|---|---|
| A: filtergraph + libx264 slow crf17 | 17.9 s | 1.12x realtime | 16.9 MB | 98.0 |
| **B: pipe + libx264 slow crf17** | 21.6 s | 0.92x realtime | 16.8 MB | 97.9 |
| B: pipe + VideoToolbox q65 | 5.1 s | 3.92x realtime | 9.4 MB | 96.0 |
Decode + compose alone: 119 fps (decode 184 fps).

## Decision
B. It costs about 20% over the pure filtergraph and gives exact control of every layout, the debug render, and
testability. Profiling found the first version's bottleneck: `cv2.warpAffine` with Lanczos ran at **35 fps**; separable
`cv2.resize` with Lanczos on a whole-pixel crop runs at **214 fps** (compose alone 30 -> 119 fps). The price is crop
positions quantised to one source pixel (<= 0.5 px, about 0.9 px in the 1080-wide output), invisible at the camera
speeds used. Static-crop clips could take path A as a fast path later (about 20% saving); not done.

## Frame accuracy
Each segment is decoded with `-ss` before `-i` (frame-accurate on re-encode) and exactly `round(length*fps)` frames are
read, so output frames equal the timeline length. Measured A/V drift on 12 real renders: 0.0 to 0.5 frame.
