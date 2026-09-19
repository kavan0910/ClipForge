# ADR-002: ffmpeg build (libass, zimg)

- Status: **open** (needs the owner's go-ahead before the environment is changed)
- Date: 2026-09-19

## Context
The installed Homebrew ffmpeg 9.0.1 has libx264, VideoToolbox and loudnorm but no libass
(no `subtitles`/`ass` filters) and no zimg (no `zscale`). Consequences: no ASS fallback captions,
and no correct HDR to SDR tone-mapping. Not needed for Phase 1; needed in Phase 3 (HDR) and Phase 4 (ASS).

## Options
1. Homebrew tap `homebrew-ffmpeg/ffmpeg` with libass and zimg (replaces the formula; changes the user's system).
2. A separate project-local static arm64 build under `.tools/ffmpeg` (does not touch the system ffmpeg).
3. Skip libass: rasterise ASS with a standalone libass binding and overlay it (still needs zimg for HDR).

## Decision
Pending. Recommended: option 2, verified with `clipforge doctor` (filters present) and a hash check of the download.
