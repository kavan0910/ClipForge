# Phase 3 checkpoint: cut, reframe, audio

**Status: acceptance rows met on 14 real clips (M1 8 GB). Multi-speaker layouts are verified on synthetic scenes only.**

## Acceptance (`scripts/report_phase3.py`, 14 clips from 6 real videos, all rendered with cleanup=light)
| Section 12 row | Requirement | Measured |
|---|---|---|
| Cuts | zero mid-word cuts | **0** mid-word cuts over every EDL boundary (also property-tested) |
| Cuts | no audible click at any cut | worst waveform-discontinuity ratio **0.60** (limit 1.5); an unfaded control cut measures >2x higher |
| Sync | A/V drift < 1 frame | worst **0.5** frame (typ. 0.0-0.2) |
| Framing | face in crop >= 98% of frames | min **98.5%**, most 99-100%, measured by re-detecting faces on the RENDERED frames (independent of the analysis that drove the crop) |
| Framing | jerk under a defined threshold | defined in `reframe/camera.py`: p99 <= 60 and max <= 100 frame-widths/s^3; worst p99 **26.1**; jitter of 2 px scores 10x over the limit |
| Framing | no layout change faster than 1.2 s | shortest layout run 27.8 s in these clips (hysteresis merges shorter runs; unit-tested) |
| Framing | eyes within 30-50% of frame height | mean **98.8%** of frames in band (per-clip min 94%), median 0.33-0.35 |
| Audio | -14 LUFS +/-1, true peak <= -1 dBTP | **-14.0 to -14.3 LUFS**, worst true peak **-2.4 dBTP**, measured on the final AAC file |
| Video | 1080x1920 H.264 High yuv420p BT.709, CRF <= 18 | all 14: `1080x1920 h264/High yuv420p bt709/bt709/bt709`, CRF 17 preset slow |
| Video | source frame rate preserved | 23.974 stays 23.974 (NTSC rates snap to exact fractions) |
| Video | HDR to SDR | decode chain verified against an SDR reference (tone-mapped is 40%+ closer than untouched); no real HDR clip rendered end to end |
| Performance | 60 s render <= 90 s | **1.47 s per clip-second on M1 8 GB = 88 s for 60 s** (borderline); Fast mode (VideoToolbox) 3.9x realtime |

Real layouts exercised: `single` (Kende, Hannah, Flynn presenter, Flynn inset) and `fit_blur` (screen recording, two
graphics-only podcasts). Visual check of output and debug frames confirmed framing.

## What was built
EDL with property-tested remapping (words, series, cut points, remove/restore); cleanup planner (fillers, repeats, false
starts, pause compression; never trims a clip's first/last word); ByteTrack-style tracker; SCRFD detection (ADR-005);
mouth-motion active-speaker vote; layout engine (single / two-shot / stacked / screen+cam / blurred fit) with 0.4 s
switch delay, 1.2 s dwell and hysteresis; camera (dead zone, velocity/acceleration limits, cut vs pan, lead room, eye-line
zoom); frame-accurate single-encode renderer (ADR-004) with debug render; click-free EDL audio joins, two-pass loudnorm
with a closed loop and safety limiter; encoder modes (ADR-009); `clipforge render` with `--fast --debug --punch-in`.

## Findings worth knowing
- **Whisper drops fillers.** Only 3 "um/uh" in ~15,000 words across the six videos (a disfluency prompt found 1 more).
  So cleanup is mostly pause compression today; filler removal needs a verbatim-leaning ASR or acoustic filler detection.
- Compose was 30 fps because `cv2.warpAffine` + Lanczos is slow; separable `cv2.resize` is 6x faster (ADR-004).
- The first true-peak numbers failed (-0.2 dBTP after AAC); fixed by lower loudnorm TP target, a -3 dBFS safety limiter
  and a closed loop that re-measures and trims gain (LUFS -15.2 -> -14.2).
- A talking head near an edge was misclassified as a facecam (static + near edge); a facecam must also be small.
- `python -m clipforge.cli` ran before later commands were defined (main guard mid-file); fixed and tested via the cancel test.
- OpenCV and onnxruntime in one process crash at exit; all OpenCV/onnx work runs in subprocesses.
- Kende's head sits high in frame: the 1.35x zoom cap limits how far the eye line can drop (median 0.347).

## Known gaps (not hidden)
- **Active speaker on real multi-speaker video is unmeasured.** Two-shot, alternating, stacked and screen+cam are proven
  on synthetic scenes and pixel-level composition tests only; no CC conversation fixture was rendered.
- No real facecam/gameplay fixture; facecam detection thresholds are untuned on real footage.
- Speech enhancement (RNNoise/DeepFilterNet) and the AI upscaler (ADR-008) are not implemented.
- No saliency crop for no-face motion footage; the blurred fit-to-width fallback is used instead.
- A 3-hour source was not run (every stage streams, but the RSS claim is unmeasured).
- All performance numbers are M1 8 GB; re-run `scripts/bench_render.py` and the batch on the M5.
- Oracle clips (hand-labelled moments) were rendered because the LLM has no credit; real curated clips go through the same path.
- Render is not yet a job-runner/API stage or cached via stage.json (Phase 5).
