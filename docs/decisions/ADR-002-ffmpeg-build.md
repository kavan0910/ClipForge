# ADR-002: ffmpeg build (libass, zimg)

- Status: accepted
- Date: 2026-09-19

## Context
The Homebrew `ffmpeg` formula lacks libass (no `subtitles`/`ass` filters) and zimg (no `zscale`),
so ASS fallback captions and correct HDR to SDR tone-mapping were impossible.

## Options tried
1. `homebrew-ffmpeg/ffmpeg` tap with `--with-zimg --with-libvmaf`: **failed**. Options force a source build
   and Homebrew refused: "Your Command Line Tools are too outdated" (16.3, needs 26.6). Tap removed, ffmpeg restored.
2. Homebrew core `ffmpeg-full` (bottled, no compile): **worked**.
3. Jellyfin portable static build (`jellyfin-ffmpeg_*_portable_macarm64-gpl`): viable fallback, not needed.

## Decision
Replace `ffmpeg` with `ffmpeg-full` (owner approved replacing the system ffmpeg).
Verified on this machine: 9.0.2 with `subtitles`, `ass`, `zscale`, `tonemap`, `libplacebo`, `libvmaf`, `rubberband`,
`loudnorm`, libx264, `h264_videotoolbox`, libvpx-vp9. A real HDR (PQ/BT.2020) clip is detected, tone-mapped to SDR
BT.709 8-bit in the proxy path (`test_hdr_source_is_detected_and_tonemapped_to_sdr`).

## Consequences
- `make setup` installs `ffmpeg-full` (see README); `clipforge doctor` still flags a build without these filters.
- `libvmaf` enables the quality comparison in ADR-009 (VideoToolbox vs libx264).
- Cost: larger install and disk use (~ 4 GiB of dependencies).
