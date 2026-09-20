# ADR-006: how the Remotion caption layer reaches the single encode

- Status: accepted
- Date: 2026-09-20

## Options measured (10 s karaoke-pop clip, 300 frames at 30 fps, 1080x1920, M1 8 GB, `captions/render.mjs`)
| Transport | Render time | Output | Overlay decode cost (10 s) |
|---|---|---|---|
| VP9 alpha WebM, full frame (`renderMedia`) | **96.7 s** | 0.64 MB | 0.15 s (libvpx) |
| VP9 alpha WebM, caption band only (614 px tall) | 39.0 s | 0.51 MB | 0.15 s |
| PNG sequence, full frame (`renderFrames`) | 8.4 s | 300 files, 20 MB | - |
| **Unique visual states as PNG stills + ffconcat** (band only) | **~10 s incl. bundling** | 92 stills for 300 frames | 0.05 s |
ProRes 4444 was excluded up front (file size).

## Finding
Chromium screenshots are cheap (36 fps for full-frame PNGs). The slowness of the obvious approach is Remotion's
**VP9-alpha encode**. Most frames are identical anyway: only the active word and 3-5 frame animations change, so
a 60 s clip needs about 550 distinct stills, not 1800 frames.

## Decision
`captions/render.mjs` `states` mode renders only the distinct states listed in `props.times`; Python writes an
ffconcat list that holds each still for its duration; ffmpeg overlays it inside the same encode as the video
(`overlay=0:y0:format=auto`). No intermediate video is encoded, the picture is compressed once, disk use is small.
The band is 30% of the frame height for captions and 26% for the hook (separate layers).

## Costs
Fonts are loaded from `/public/fonts` by Chromium; Node 20+ and the bundled Chromium headless shell are required.
Without them, `--renderer auto` falls back to the ASS renderer. A 27.8 s clip rendered 46 s with Remotion captions vs 40 s
with ASS.
