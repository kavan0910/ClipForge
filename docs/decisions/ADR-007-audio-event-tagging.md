# ADR-007: laughter and applause tagging

- Status: accepted, with a stated evidence gap
- Date: 2026-09-19

## Context
Signals need per-second laughter and applause probabilities. Candidates: PANNs CNN14 (PyTorch, already a
dependency through pyannote) and YAMNet (TFLite via `ai-edge-litert`; TensorFlow itself was excluded as too heavy).

## Benchmark (`scripts/bench_events.py`, CPU, M1)
| Model | Speed | Crowd laughing+clapping clip | Interview speech (false positives) |
|---|---|---|---|
| PANNs CNN14 (32 kHz) | ~160-220x realtime | Applause 0.67-0.76, Laughter ~0.04 | max 0.00-0.01 |
| YAMNet TFLite (16 kHz) | ~1100x realtime | max 0.15 (weak) | max 0.00 |

## Evidence gap (important)
The only positive clip I could license (Commons `laughter_crowd.wav`, 9.9 s, "crowd laughing and clapping") is
mixed, and the clip titled "applause" (37 s) is dominated by speech (PANNs clipwise Speech 0.84). A stand-up clip
had no audible laughter either. So there is **no trustworthy labelled set**: recall for laughter is unmeasured, and
the numbers above show sensitivity on one clip plus a low false-positive rate on speech, not accuracy.
(I also caught a bug in my own first run: PANNs needs 32 kHz input; 16 kHz gave meaningless zeros.)

## Decision
Use PANNs CNN14 sound-event detection. It is far more sensitive on the one positive clip and fast enough (a
3-hour source tags in about a minute). `laughter = max(Laughter, Belly laugh, Chuckle, Giggle, Snicker)`,
`applause = max(Applause, Cheering, Clapping)`, both max-pooled per second. Both feed the interest curve and the
`{laughter}` / `{applause}` tags at thresholds of 0.3.

## Follow-ups
- Build a labelled laughter set from CC comedy or panel recordings (Phase 2 eval backlog) and re-measure recall.
- If YAMNet's speed matters more than sensitivity, revisit with a calibrated threshold.
