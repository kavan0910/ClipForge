# ADR-008: optional AI upscaler for sub-1440p sources

- Status: **deferred** (not implemented, not benchmarked)
- Date: 2026-09-19

The brief asks for an optional upscaler flag with a benchmark and licence record. It was not built in Phase 3: every
real fixture is 1080p or below, and a vertical crop of 1080p is upscaled ~1.8x by Lanczos, which looked acceptable in the
renders (see `docs/checkpoints/phase-3.md`). The quality report already warns when the source is below 1440p.
Candidates for later: Real-ESRGAN class models (BSD-3-Clause code; check the weights' licence), run only on the crop
region. Needs a torch/MPS benchmark for speed (per-frame cost is likely far above the 119 fps compose stage).
