# Phase 0 checkpoint

**Works**
- Repo layout, uv (Python 3.12) with pinned deps in `uv.lock`, Vite React TS Tailwind TanStack Query web shell, Remotion installed in `captions/` (Node 20.19 accepted it), Makefile, `.env.example`, GitHub Actions CI, ADR template.
- `clipforge doctor` reports hardware, disk, ffmpeg capabilities (libx264, loudnorm, libass, zimg, VideoToolbox), yt-dlp version and JS runtime, Node, API keys, each with a fix.
- Fixture fetcher pulls two CC BY 3.0 1080p interviews from Wikimedia Commons (626 s and 235 s), verifies live licence and records sha256.
- Gates: `make lint` (ruff, ruff format, pyright, oxlint, tsc) and `make test` (pytest 4 passed, vitest 1 passed) are green.

**Pinned (installed today)**: yt-dlp 2026.08.19, anthropic 1.7.0, fastapi 0.141.1, pydantic 2.13.5, sqlmodel 0.0.42, pytest 9.1.1, hypothesis 6.168.0, ruff 0.16.8, pyright 1.1.414, Vite 8.3.0, React 19.3, Tailwind 4.3.3, Vitest 4.1.11, Playwright 1.63, Remotion 4.0.526 (custom licence, see captions/README.md).

**Measured (M1 8 GB dev machine)**: 37 GiB free, no libass, no zimg, deno 2.9.7 installed for yt-dlp.

**Known gaps**
- ffmpeg lacks libass and zimg on this machine (ADR-002 pending, Phase 1/3).
- Synthetic face-track generator arrives with the layout engine (Phase 3), where its schema is defined.
- Fixtures cover interviews only; a screenshare/gameplay fixture is still to be found and licence-checked.
- CI has not run (no remote).
