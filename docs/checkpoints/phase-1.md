# Phase 1 checkpoint: ingest and transcript

## Acceptance
| Criterion | Result |
|---|---|
| Link and upload each produce the same `Source` and a `transcript.json` with word timestamps | **Pass.** Playwright compares the key sets of source, probe, quality and transcript for a real YouTube link (Me at the zoo) and a real 45 s upload: identical. |
| Progress visible in the UI, Playwright tests for both flows | **Pass.** 5/5 Playwright tests (tabs/keyboard/privacy; link; upload; schema parity; interrupted upload resumes at the server offset). |
| Kill mid-download then resume | **Pass.** Real 1080p Big Buck Bunny: killed at 14 MB of partial data, no orphan yt-dlp/ffmpeg, resume finished the acquire stage in 6.4 s. |
| Kill mid-ASR then resume | **Pass.** `test_e2e_resume` (slow): 14.3 min audio in 2 chunks, killed after chunk 1, worker gone, chunk 1 not recomputed (mtime unchanged), no duplicated words at the seam. It found and fixed two real bugs (orphaned worker; NaN scores). |
| 30 GB-class upload, flat memory | **Pass.** 12 GiB streamed to disk in 21 s (582 MiB/s), peak RSS growth 8 MiB. |
| ASR real-time factor, WER | Reported (M1, 8 GB): turbo 5.6x realtime inside the pipeline (235 s in 41.8 s incl. model load); 6.4x in isolation; large-v3 0.8x. Turbo vs large-v3 disagreement 4.4% WER. No human reference exists, so absolute WER is **not** measured yet (Phase 2 eval). See ADR-001. |

## Measured pipeline timings, 3:55 1080p interview (M1, 8 GB)
acquire 0.0 s (symlink), audio 0.4 s, proxy 17.8 s, transcribe 41.8 s. 569 words / 21 sentences.

## Bugs found by real runs (not by unit tests)
1. `.env.example` inline comment after an empty value leaked into `YTDLP_COOKIES_FROM_BROWSER`, breaking every link resolve. Fixed; empty values now count as unset (test added).
2. ASR worker orphaned when its parent was killed (own session). Now: signal handlers cancel cleanly and the worker exits when orphaned.
3. NaN/inf Whisper scores broke the chunk JSON. Sanitised.
4. Big Buck Bunny (almost no speech) produced 194 hallucinated words. Added a Silero VAD guard: now 0 words; the interview keeps 565 of 569.
5. Cached raw ASR chunks were not keyed by parameters (stale reuse). Now keyed by model, prompt, language and chunk plan.
6. Finished job processes stayed as zombies and read "running". Reaped; status now derives from the event log.
7. Unknown `/api/...` paths fell through to the SPA (200). Now 404.

## Gates
ruff, ruff format, pyright, oxlint, tsc: clean. pytest 66 passed (+2 slow/live run separately), vitest 3, Playwright 5.

## Known gaps
- Diarization untested end to end: the HF token gets 403 until the pyannote terms are accepted (https://hf.co/pyannote/speaker-diarization-community-1). Degrades to single-speaker with a UI warning.
- ffmpeg lacks libass/zimg (ADR-002 open); HDR tone-mapping unmeasured.
- Word-onset accuracy (+/-50 ms) not yet measured (Phase 4 uses an energy-onset check, ADR-003).
- ASR numbers are from the M1 dev machine; re-run `make bench-asr` on the M5.
- No SQLite index yet; no Settings screen; live-chat replay not downloaded.
- Playwright link test depends on live YouTube and network.
