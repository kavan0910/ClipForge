# Clipforge Plan

Status: approved 2026-09-19. Read with `ARCHITECTURE.md`. Each phase ends with: commit, tests green, ruff/pyright/vitest/tsc clean, a manual run on real footage with measured numbers, a checkpoint note in `docs/checkpoints/phase-N.md` (what works, what was measured, known gaps).

## Working rules

- Core logic is test-first: EDL remap (property-based via Hypothesis), boundary snapping, layout engine, schema validation, cost meter, cancel/kill.
- No mock data paths in delivered features. Stubs go in README "Known gaps".
- Fixtures: CC/public-domain only, listed with licence and source URL in `eval/fixtures/MANIFEST.json`, fetched by `scripts/fetch_fixtures.py` (not committed). Synthetic face-track generator is committed.
- Real LLM calls in tests are opt-in (`-m live`); default CI replays recorded responses.
- The repo is not yet a git repository; Phase 0 starts with `git init` (needs your OK only because you did not ask for it; I will do it unless you object).

## Phase 0: plan and scaffold  (this phase)

Deliverables now: `ARCHITECTURE.md`, `PLAN.md`, self-review below. **Stop for your approval.**
After approval, the scaffold part of Phase 0: repo layout, `pyproject.toml` (uv, py3.12), Makefile, `.env.example`, ruff/pyright/pytest config, `web/` Vite+React+TS+Tailwind+TanStack Query shell, `captions/` Remotion skeleton, GitHub Actions CI (lint, types, unit tests, web build), `scripts/doctor` + `clipforge doctor`, fixture fetcher and synthetic face-track generator, ADR template.
*Acceptance:* `make setup && make lint && make test` green on a clean checkout; `clipforge doctor` correctly reports on this machine: no libass, no zimg, no yt-dlp, no JS runtime, RAM 8 GB, disk 38 GB free, with the fix for each; fixtures fetched and checksummed.

## Phase 1: ingest and transcript

Both providers to the same `Source`; ffprobe validation and quality report; proxy + 16 kHz wav; HDR/VFR/rotation/SAR/multi-audio handling (HDR tone-mapping gated on ADR-002); chunked resumable upload; import-by-path; yt-dlp resolve/fetch with SSE progress, retries, actionable errors, platform signals; ASR (ADR-001, 003) with chunk+merge, hallucination guards, glossary, optional diarization; stage cache; cancel/resume; minimal UI shell with both tabs; Playwright tests for both flows.
*Accept:* YouTube link and file upload each yield an identical-schema `Source` and a `transcript.json` with word timestamps; progress visible in UI; kill mid-download and mid-ASR then resume; 30 GB-class behaviour proven by a sparse-file streaming test (memory stays flat, measured RSS); ASR real-time factor and WER reported.

## Phase 2: signals and curation

Signals (ADR-007), sentence segmentation, two-stage curation with forced-schema output, repair retry, prompt caching, deterministic post-processing, cost meter and hard cap, steering, re-curation, hook-evidence check, eval harness v0.
*Accept:* clip list JSON with valid boundaries and scores; 100% of clips start on a sentence start and no mid-word cut (asserted in code); costs recorded and equal to API `usage` sums; eval report for prompt v1 (recall@k at 50% overlap on the hand-labelled set, boundary %, hard-reject pass %); 1 h Balanced cost measured vs the $0.25 gate.

## Phase 3: cut, reframe, audio

EDL + remap tests; frame-accurate cuts (ADR-004); face/track/active-speaker (ADR-005); layout engine incl. screen+cam facecam detection; camera smoothing; debug render; filler/pause cleanup with restore list; two-pass loudnorm; encode; Fast mode (ADR-009).
*Accept:* captionless vertical clips meeting the Framing, Cuts, Sync, Audio, Video rows of Section 12 with numbers: face-in-crop %, jerk p99, min layout dwell, cut-point discontinuity, A/V drift, LUFS/TP, ffprobe stream facts. 3-hour source completes with peak RSS reported (on 8 GB this is the real test).

## Phase 4: captions and packaging

Template spec + JSON schema; ASS renderer; Remotion renderer + `@remotion/player` preview; 8 templates; brand kits; hook overlay; thumbnails; sidecars; single-encode compositing (ADR-006); font bundling with licences, CJK/RTL fallback.
*Accept:* Captions row of Section 12 (2 lines max, safe zones, long-word overflow test, CJK and RTL samples, light/dark contrast check), onset error within +/-50 ms measured, preview-vs-export frame diff under threshold.

## Phase 5: review UI

Review grid, clip editor with text-based trim, layout overrides, restore fillers, re-curation bar, keyboard map, render queue, export, OTIO/FCPXML/EDL export (validate by re-importing with OpenTimelineIO; Resolve/Premiere import verified manually if available).
*Accept:* link or file to approved, exported clips entirely from the UI and keyboard-only; Playwright covers both flows end to end.

## Phase 6: polish and optional publishing

YouTube publisher (OAuth, resumable upload, scheduling; needs your Google Cloud client), IG/TikTok documented stub + upload package, audiogram layout, docs, packaging, performance pass vs Section 12 targets, final report.

## Blocking inputs I will need from you (later, not now)

- `ANTHROPIC_API_KEY` at the start of Phase 2 (Phase 1 needs none).
- Phase 1 (optional): `HF_TOKEN` with pyannote terms accepted; otherwise single-speaker mode.
- Phase 6 (optional): Google OAuth client for YouTube upload.
- Permission to install system tools on this Mac (`uv`, `deno`, a full ffmpeg build with libass/zimg). I will not touch your existing Homebrew ffmpeg without asking; the default plan installs a separate binary under the project (ADR-002).

## Decisions I need you to confirm at approval

1. **Hardware reality.** This is an M1 with 8 GB and 38 GB free. Section 12 targets (ASR >= 10x realtime with large-v3, 3 h source without OOM, 60 s render <= 90 s) are at risk. I will report actuals and adjust defaults (e.g. large-v3-turbo) via ADRs rather than pretend. Is this the machine you will run on, or is there a beefier one?
2. **ffmpeg with libass + zimg** as a project-local second binary (my default) vs replacing Homebrew's.
3. **Git**: initialise the repo here.

## Self-review (plan-eng-review style) and what changed

Findings from critiquing my own draft, and the fix applied:

1. **The brief's performance and robustness targets were not reconciled with the actual machine.** Measured 8 GB/38 GB. *Fix:* added the resource-class scheduler, memory guard, disk preflight, R1/R2/R5, and made target attainment a reported number.
2. **"Single encode" hides the hardest engineering problem** (time-varying crop and layouts in ffmpeg). Original plan assumed a filtergraph. *Fix:* ADR-004 benchmarks filtergraph vs raw-frame pipe before Phase 3 code, with a frame-accuracy test as the gate.
3. **Missing libass/zimg would have silently broken the ASS fallback and HDR tone-mapping.** *Fix:* `doctor` detection, ADR-002, features gated with clear messages.
4. **Caption alpha layer could exhaust disk** (PNG sequences). *Fix:* pipe/band-only/VP9-alpha benchmark (ADR-006).
5. **Framing metric was self-graded** (measuring the crop against the same detector that drove it). *Fix:* face-in-crop measured against hand-labelled frames; jerk metric defined now.
6. **Hook truthfulness had no mechanism.** *Fix:* `hook_evidence` sentence range and a deterministic check.
7. **Cost gate had no margin analysis.** *Fix:* token estimate (~$0.15/h Balanced), verified on real fixture in Phase 2; the hard cap checks worst-case before each call.
8. **A localhost service is still attackable** (CSRF, DNS rebinding, SSRF via yt-dlp URL). *Fix:* per-run token, Host-header check, public-address check on fetched URLs.
9. **Fixture and label sourcing is a schedule risk** (R10/R11). *Fix:* start with 3 hand-labelled videos in Phase 2, licence manifest, grow later.
10. **Eval in CI would burn API money.** *Fix:* record/replay of LLM responses; live runs opt-in.
11. **Tension in the brief:** "Stage A cheap scan" for a 1 h video costs more than the single-pass cutoff suggests; noted that Balanced is only ~$0.15/h, so Economy exists mostly for very long sources.
12. **Ambiguity left as default:** Sonnet 5/Opus 5 model IDs come from the current API reference; re-verified at implementation with a one-call probe before Phase 2 relies on them.

Residual risks I cannot remove by planning: the M1/8 GB ceiling (R1, R2, R3) and editorial quality (R7). Both are measured, not assumed, from Phase 1 and 2.
