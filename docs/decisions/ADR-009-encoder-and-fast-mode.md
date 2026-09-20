# ADR-009: encoder settings and Fast mode

- Status: accepted
- Date: 2026-09-19

## Quality mode (default)
libx264, High profile, `-preset slow -crf 17`, yuv420p, BT.709 signalled in the bitstream
(`colorprim/transfer/colmatrix=bt709`, verified with ffprobe on the output), +faststart, AAC 192 kbps.
Source frame rate is preserved (23.974 stays 23.974; NTSC rates snap back to exact `24000/1001` fractions).

## Fast mode (`--fast`)
`h264_videotoolbox -q:v 65 -profile:v high` (Apple hardware encoder), same colour tagging and audio.

| | libx264 slow crf17 | VideoToolbox q65 |
|---|---|---|
| Time for a 20 s 1080x1920 clip (M1) | 21.6 s | 5.1 s (4.2x faster) |
| Size | 16.8 MB | 9.4 MB |
| VMAF vs lossless (same frames) | 97.9 | 96.0 |

VMAF 96 is visually transparent for talking-head content at this size, so Fast mode is offered for drafts and bulk
export; Quality mode stays the default for final delivery. `-q:v 65` was picked as a single point on the trade-off,
not swept. `hevc_videotoolbox`/NVENC were not evaluated.
