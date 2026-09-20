# Phase 2 checkpoint: signals and curation

**Status: built and tested offline; the live-LLM acceptance items are BLOCKED on API credit.**
The Anthropic key in `.env` returns `400: credit balance is too low` on every model (probed 3 times). Add credit
under Plans & Billing, then run the three commands at the bottom.

## Acceptance
| Criterion | Result |
|---|---|
| Signals (audio, visual, platform) | **Pass.** Energy, speech rate, density, laughter/applause (PANNs), shot changes + motion (OpenCV in an isolated subprocess), heatmap/chat/chapters when present. 235 s video: 13 s (18x realtime). Interest curve + per-sentence tags `{energy:high, laughter, heatmap:0.83}`. |
| Two-stage curation, forced schema, repair retry, prompt caching | **Built, plumbing tested with a protocol-level stand-in, live behaviour unverified.** 12 tests: chunking, cache_control placement (transcript block cached, steering after it), one repair retry then a clear error, json-schema to forced-tool fallback, raw responses stored per run, presets, cost cap. |
| Clip list JSON with valid boundaries and scores | **Boundaries verified on real data**: the 31 hand-labelled moments across 6 real transcripts, fed as perfect proposals, all resolve to clean cuts: 100% start on a sentence boundary, 0 cuts inside a word, all 20-90 s. Scores come from the LLM, so unverified. Property tests (Hypothesis) show cuts never land inside a word for random word/pause layouts. |
| Costs recorded, equals API `usage` sums | **Built, unverified live.** Meter reads `input/output/cache_creation/cache_read` usage fields; prices from the published table; hard cap checks worst case before each call (test: a $0.001 cap aborts with an actionable message). Live cost event stream to the UI. |
| Eval report for the first prompt version | **Blocked.** Harness, metrics, record/replay cache, 6 videos and 31 labelled good + 18 bad moments are ready. `clipforge eval core --live` records the responses. Until then the only eval numbers are the oracle numbers above. |
| 1 h Balanced cost vs $0.25 | **Unmeasured.** Estimate ~$0.15 (see ARCHITECTURE). Will be measured on `direct_current_rooftop_solar` (24.6 min) and `flynn_right_to_research` (33.7 min), then extrapolated. |
| Steering and re-curation | **Built**: `--steer`, presets, modes more_like / shorter / different_topic (excludes existing clips, no upstream rework), CLI `clipforge curate`, API `/recurate`, UI buttons. Unverified live. |

## Decisions and findings
- ADR-007: PANNs CNN14 for laughter/applause. Honest gap: no trustworthy labelled positive clips exist; recall is unmeasured.
- I caught a bug in my own benchmark (PANNs needs 32 kHz; 16 kHz gave meaningless zeros).
- OpenCV and PyAV each bundle libavdevice; loading both in one process risks crashes, so OpenCV work runs in its own subprocess.
- Diarization now works (pyannote community-1): 2 speakers on the interview, 5.5x realtime on MPS vs ~1.4x CPU.
- ffmpeg replaced with `ffmpeg-full` (ADR-002); the tap build failed on outdated Command Line Tools.
- Min-max normalisation stretched tiny LLM score gaps to 0-1, defeating diversity; added a minimum range (20 points).
- A curation failure (no credit) made the whole job "error". Fixed: it is now an isolated stage error with a retry button; transcript and signals stay usable.

## Eval set (v0)
6 CC/PD videos, 5 genres: 2 interviews, tutorial, 2 podcasts, lecture. **Labels were written by me from transcripts; they are subjective and not creator-published-Shorts ground truth** (the brief's preferred source). Treat recall numbers as a first baseline only.

## To finish Phase 2 once credit exists
```
uv run pytest -m live                    # verifies structured-output mode per model, cache hits, usage fields
uv run clipforge eval core --live        # records LLM responses, writes eval/results/v1/...
uv run clipforge curate <project> --preset balanced   # per-run cost printed from real usage fields
```

## Known gaps
- Face presence signal is not computed in Phase 2 (comes from the Phase 3 face tracker).
- Live-chat replay is flagged but not downloaded; the rate extractor is implemented and tested on a synthetic file.
- Pairwise LLM judge prompt exists (`prompts/v1/judge.md`) but the judge runner is not wired (needs the API).
