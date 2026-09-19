# ADR-001: ASR backend and default model

- Status: accepted (provisional until re-run on the M5 target machine)
- Date: 2026-09-19

## Context
Word-level timestamps are mandatory. Target hardware is Apple Silicon (M5, 16 GB); development
ran on an M1 with 8 GB. CUDA is not available, so mlx-whisper is the primary backend and
faster-whisper (CPU int8 or CUDA) is the portable fallback behind the same `ASRBackend` worker.

## Benchmark (scripts/bench_asr.py, 180 s excerpt of `kende_internet_hall_of_fame.webm`, M1 8 GB)
| Model | Wall time | Speed | Words | Word agreement vs large-v3 |
|---|---|---|---|---|
| mlx-community/whisper-large-v3-mlx | 223 s | 0.8x realtime | 432 | reference (WER 0.0) |
| mlx-community/whisper-large-v3-turbo | 28 s | 6.4x realtime | 423 | WER 4.4% |

Caveats, stated plainly:
- Wall time includes model load and first-run Metal compilation, and large-v3 (fp16, ~3 GB weights)
  ran under memory pressure on 8 GB, so 0.8x is a pessimistic bound. The M5/16 GB figure must be re-measured (`uv run python scripts/bench_asr.py 180`).
- There is no human reference transcript. "WER vs large-v3" measures agreement between models,
  not accuracy against truth. Accuracy against a hand-corrected transcript is a Phase 2 eval task.
- The first run mislabelled RSS units (values are MiB: 870 and 1777). Metal peak memory was
  not captured in run 1; the script now records it (`mx.get_peak_memory`).

## Decision
Default `ASR_MODEL=large-v3-turbo` on mlx-whisper. It is the only tested option near the 10x
realtime target on this hardware and disagrees with large-v3 on ~4% of words, most of which
are punctuation-level or filler differences. `large-v3` stays selectable (`ASR_MODEL=large-v3`)
for accuracy-critical runs and will be re-benchmarked on the M5; if large-v3 reaches >= 10x
there, the default flips back.

## Consequences
- Curation quality depends on transcript accuracy; turbo's small accuracy loss is measured in eval.
- Chunked, resumable execution (10 min chunks, 15 s overlap) is independent of the model.
- Word-onset accuracy (+/-50 ms) is verified separately (ADR-003) using an energy-onset check.
