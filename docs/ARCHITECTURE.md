# Clipforge Architecture

Status: approved 2026-09-19 (target hardware updated to M5 16 GB).

## 0. Target and development hardware

**Target machine (per owner, 2026-09-19): Apple M5, 16 GB RAM, 70-80 GB free disk.** Consequences: `RENDER_WORKERS` defaults to 2, large-v3 fits in memory alongside one other heavy stage, and the disk risks (R5) are smaller but the preflight stays. The 3-hour-source and 10x-realtime targets are re-evaluated on the M5. **The measurements below were taken on the current development machine (M1, 8 GB), so any performance numbers reported until the code runs on the M5 are labelled M1 lower bounds.**

## 0.1 Measured environment (development machine, M1 8 GB, 2026-09-19)

| Item | Finding | Consequence |
|---|---|---|
| CPU/RAM | Apple M1, **8 GB** unified memory, arm64 | No CUDA. ASR = mlx-whisper. Memory is the binding constraint: ASR, Chromium (Remotion) and ffmpeg must never run concurrently by default. `RENDER_WORKERS` defaults to 1 when RAM <= 8 GB. |
| Disk | 228 GB volume, **38 GB free (82% used)** | 30 GB uploads and 3 h sources are only possible with reference/symlink import and aggressive temp cleanup. Disk-space preflight is a hard gate in every stage that writes big files. |
| ffmpeg | 9.0.1 (Homebrew), has libx264 + VideoToolbox, `loudnorm`, `tonemap`. **No libass (`subtitles`/`ass` filters missing), no zimg (`zscale` missing), no libplacebo.** | (a) The ASS fallback renderer cannot use ffmpeg's `subtitles` filter on this install. (b) HDR to SDR via `zscale`+`tonemap` is unavailable. `doctor` must detect both and `make setup` must install a full ffmpeg build (see ADR-002). Until then these features are gated, not faked. |
| Python | system 3.14.3; `uv` **not installed** | Project pins 3.12 via uv. `make setup` installs uv. |
| Node | 20.19.3; no deno/bun | Enough for Vite and (to be verified) Remotion. yt-dlp's YouTube extraction may need an external JS runtime: `make setup` installs deno; `doctor` checks it. |
| yt-dlp | not installed | Pinned and installed in the venv by `make setup`. |
| Skills | Only `resume-match`, `writing-great-skills`, `synced` exist in `~/.claude/skills`. None of the skills the brief names are present. | Skipped silently as instructed; I follow their spirit (TDD on core logic, verification loop each phase). |

Not yet verified (deliberately deferred to install time, when the docs can be read against the real package): exact versions and licences of yt-dlp, faster-whisper, mlx-whisper, WhisperX, pyannote.audio, Remotion, OpenTimelineIO, PySceneDetect, MediaPipe/InsightFace/ultralytics, the Anthropic SDK. Each is pinned in `pyproject.toml`/`package.json` in Phase 1 with the version recorded in `docs/decisions/`.

Model IDs and prices used for planning (from the current Claude API reference): `claude-haiku-4-5` $1/$5 per M tokens (in/out), `claude-sonnet-5` $2/$10, `claude-opus-5` $5/$25. The brief's dated Haiku ID `claude-haiku-4-5-20251001` is kept as the `.env.example` default because you specified it; both IDs are configurable, and the client reads real `usage` fields, not these estimates. Sonnet 5 and Opus 5 reject `temperature/top_p/budget_tokens` and prefill; curation code must not send them. Forced `tool_choice` works on Sonnet 5/Haiku 4.5/Opus 5 per the docs I have; if a chosen model rejects it, fall back to `output_config.format` structured output (decided at implementation via a one-call probe).

## 1. Components

```
                +-------------------------- web/ (Vite React TS) --------------------------+
                |  New project | Processing | Review+Editor | Export | Settings            |
                |  @remotion/player preview  <-- shared caption template spec (JSON)       |
                +-----------------------|-----------------------------|--------------------+
                                  REST + SSE (127.0.0.1)         static build served by API
                                        |
+---------------------------------------v--------------------------------------------------+
| backend/api  FastAPI: projects, sources, upload (tus-style chunks), jobs, clips, export  |
|   auth: none (localhost only) + per-run random token header to block DNS-rebinding/CSRF   |
+-----------|------------------------------------------------------|----------------------+
            | library calls                                        | SSE from event log
+-----------v-----------------------+                 +------------v-----------------------+
| clipforge (pipeline library)      |                 | SQLite (SQLModel)                   |
|  stages/ ingest transcribe signals|<--------------->|  projects, sources, jobs, stages,   |
|  curate edl reframe render package|                 |  clips, edits, usage, brand_kits    |
|  providers/ url upload            |                 +-------------------------------------+
|  asr/ faster_whisper mlx whisperx |
|  llm/ anthropic client, cost meter|   DATA_DIR/projects/<id>/  (files + stage manifests)
|  captions/ ass_renderer remotion  |
+-----------|-----------------------+
            | spawns (process groups, killable)
+-----------v-----------------------------------------------------------------------------+
| worker pool: subprocess per stage-run. Scheduler holds a resource lock per class:        |
|   GPU/ANE(asr, face) x1 | CHROMIUM(remotion) x1 (8GB) | FFMPEG x N | NET(llm, dl) x k      |
+------------------------------------------------------------------------------------------+
captions/  Remotion project + template-spec (JSON schema, TS types generated from it)
eval/      fixtures, labels, harness      scripts/  doctor, fixture fetchers
```

The CLI (`clipforge run|doctor|eval`) calls the same library; the API is a thin layer. No Redis: jobs are rows in SQLite, workers are child processes, progress is an append-only `events.jsonl` per job that SSE tails (so a reconnecting browser or a resumed CLI reads the same history).

## 2. Data model

Filesystem is the source of truth for artifacts; SQLite indexes them.

```
projects/<id>/
  project.json            settings snapshot, yt-dlp/ffmpeg versions, source hash
  source/  master.<ext> | master.link   proxy_720p.mp4  audio_16k.wav  probe.json  platform.json
  stages/<name>/stage.json   {stage, version, input_hash, param_hash, status, started, ended, timings, outputs[]}
  transcript/transcript.json  sentences.json
  signals/signals.parquet? -> signals.json (1 Hz curve + features)
  curation/runs/<run_id>/{request.json,raw_response_*.json,clips.json,usage.json,prompt_version}
  clips/<clip_id>/{edl.json, reframe.json, captions.json, out.mp4, thumb.jpg, .srt .vtt .ass, metadata.json}
  logs/  events.jsonl  stage logs
```

Key records (Pydantic v2, single definitions shared by API and library):

- `Source {id, kind: url|upload|path, content_hash, uri_meta, probe, has_video, audio_tracks[], selected_audio, quality_report, platform_signals}`. Both providers emit exactly this; nothing downstream branches on `kind`.
- `Word {i, w, start, end, prob, speaker}`; `Sentence {id: "S0001", word_lo, word_hi, start, end, speaker}`.
- `Clip {id, run_id, start_sentence, end_sentence, title, hook, summary, why_it_works, scores{6}, overall, signal_score, rank_score, emphasis_word_indices, description, hashtags, risk_flags, status: proposed|approved|rejected, user_edits}`.
- `EDL {clip_id, segments:[{src_in, src_out, out_in, out_out, kind: keep|cut, reason, restore_id?}], removed:[{id, kind, word_range, src_in, src_out, reason, restored}]}`. Invariants: segments sorted, non-overlapping in both axes, contiguous in output time, `out_out - out_in == src_out - src_in` (no speed change in v1). Functions: `src_to_out(t)`, `out_to_src(t)`, `remap_words()`, `remap_path()`, all pure and total.
- `CameraPath {fps, keyframes[], layout_segments:[{t0,t1,layout,params}]}` in **source time**, remapped through the EDL at render.
- `CaptionTemplate` JSON spec (Section 6).
- `Usage {job_id, stage, model, input, cache_write, cache_read, output, usd}`, priced from a versioned price table plus the API `usage` fields.

## 3. Stage contracts

Every stage: `run(ctx, inputs, params) -> outputs`, pure w.r.t. cache key `sha256(stage_version | input_hashes | canonical(params))`. Skipped when `stage.json` matches and outputs exist. Writes to `*.partial`, atomic rename on success. Cancel = SIGTERM to the process group, then SIGKILL after 5 s; children are started with `start_new_session=True`. Resume = re-run; completed stages skip, download/upload/ASR chunks resume from their own partial state.

| # | Stage | In | Out | Notes |
|---|---|---|---|---|
| 1 | ingest | URL or upload/path | master, probe, proxy, wav, quality report | Provider-specific part ends at "master exists"; everything after is shared. |
| 2 | transcribe | wav, glossary | transcript.json | Chunked with overlap, dedup by time+text at seams, hallucination guards, optional diarization. |
| 3 | signals | wav, proxy, platform.json, transcript | signals.json | Audio energy/rate/pauses/events; scenes; motion; face presence; platform. Each sub-signal independently cached. |
| 4 | curate | transcript, signals, steering, preset | clips.json | Section 4. Re-runnable without 2-3. |
| 5 | plan-edit | clip, transcript, cleanup level | edl.json | Snapping, filler/pause detection. |
| 6 | reframe | proxy, clip range, diarization | camera path, debug render | Analysis runs once per source (cached), path computed per clip. |
| 7 | render | master, edl, path, captions, brand | out.mp4 | ONE video encode. |
| 8 | package | rendered clip | thumb, sidecars, metadata, OTIO | |

Failure isolation: a stage failure marks that stage/clip failed with an actionable message; sibling clips continue; the job is `partial`.

## 4. Curation design (summary of decisions)

- Input to the model: numbered sentences `S0142 [12:31.4-12:38.9] (Speaker 2) text {energy:high, laughter}`. Output: sentence IDs only. IDs map to word indices and timestamps in code. Invalid or out-of-range IDs are rejected and trigger the one repair retry with the validation error included.
- Token budget: ~9k words/hour of speech; with IDs, timestamps and signal tags, one hour is ~35-40k tokens, so above the 30k single-pass threshold and Stage A applies. Rough Balanced estimate for 1 h: Haiku scan 40k in + ~4k out = ~$0.06; Sonnet 5 curation ~15k in + ~6k out = ~$0.09; **~$0.15 total**, under the $0.25 gate with thin margin. Prompt caching helps re-curation (cache reads) more than the first run. The meter uses real `usage` fields; the gate is verified in Phase 2 on the real fixture, not assumed.
- Prompts are files in `backend/prompts/vN/*.md` with a `PROMPT_VERSION` recorded in every run; raw responses stored.
- Post-processing (deterministic, TDD): word snap -> silence-minimum snap -> pre/post-roll -> min/max/target duration -> overlap dedupe (>30%) -> diversity -> rank blend. Labelled "Clip score (heuristic)".
- Hook truthfulness: Stage B must return a `hook_evidence` sentence-ID range; a cheap check confirms the hook's key nouns/numbers occur in that range's text, else the hook is flagged (not silently accepted). Added beyond the brief.
- Hard cap `MAX_JOB_COST_USD`: checked before every call using worst-case output tokens; aborts cleanly with a resumable state.

## 5. Reframing / camera (summary)

Analysis at 6-10 fps on the 720p proxy. Detector + tracker + lip-motion active speaker are chosen by benchmark (ADR-005). Crop path solved offline: raw target per frame -> One-Euro/Kalman with look-ahead -> dead zone -> velocity/acceleration clamp -> cut-vs-pan rule. **Jerk metric (defined now so it can be tested):** third derivative of crop-centre x in units of frame-width per s^3, computed from the final per-frame path excluding declared cut frames; pass if the 99th percentile <= 40 fw/s^3 (initial value, tuned on fixtures and recorded). Face-in-crop rate uses the same per-frame boxes as the debug render, measured against hand-labelled frames (analysis-vs-truth is separate from crop-vs-analysis, so we do not grade the detector with itself).

## 6. Render architecture: the single encode

Goal: source is decoded once and compressed once. The hard part is time-varying crop and layouts inside ffmpeg.

Candidates for the video path (benchmarked, ADR-004):
- **A. Pure filtergraph:** per-EDL-segment `trim/setpts`, crop with per-frame expressions or `sendcmd`, `scale` Lanczos, `overlay`, `concat`.
- **B. Raw-frame pipe:** ffmpeg decodes (with HDR handling) -> Python/OpenCV composes the layout (crop path, split, blurred fit, panning) -> ffmpeg encodes with captions overlaid and audio mixed. Simple, exact, testable, and easy to draw debug overlays. Cost: Python per-frame throughput on an M1.
- Provisional default: A for simple layouts if the benchmark shows exact frame accuracy, B for anything expression-hostile; decided by the numbers. Both must pass the frame-accuracy test (cut-point frame hash comparison against a reference).

Audio: separate filter chain, `atrim` + `acrossfade` (10 ms) at cuts, two-pass `loudnorm` to -14 LUFS / -1 dBTP measured on the post-EDL audio. Encode default: libx264 High CRF 17 slow yuv420p BT.709 +faststart, AAC 192k; VideoToolbox "Fast mode" with documented `-q:v`.

Captions: `CaptionTemplate` JSON (font, weight, size as fraction of frame width, case, tracking, fill, stroke, shadow, active-word treatment, in/out animation+easing, chunking, anchor, emphasis). Two renderers behind `CaptionRenderer`:
- **Remotion** (default): preview via `@remotion/player`; export renders the caption layer as an alpha sequence. **Storage risk on this machine:** 60 s at 30 fps of 1080x1920 PNG is ~1800 frames; benchmark PNG vs VP9-alpha (ffmpeg must use the libvpx decoder for alpha, native vp9 drops it) vs cropping to the caption band only. Frames stream to ffmpeg through a pipe/named pipe where possible to avoid landing on disk.
- **ASS/libass:** needs an ffmpeg with libass (missing here). Alternative if we do not want to ship a second ffmpeg: rasterise ASS with a standalone libass binding to the same alpha-overlay path. Decided in ADR-002.
- Preview-equals-export is a test: render the same frame via `@remotion/player` snapshot and via the exporter, diff with a tolerance.

## 7. API (HTTP + SSE)

Localhost only. All mutating routes require the per-run token header (mitigates CSRF/DNS rebinding against a localhost service). Errors are RFC 9457-style `{type,title,detail,action}` where `action` is the fix ("Update yt-dlp").

```
POST /api/sources/resolve {url}            -> preview card (metadata only)
POST /api/projects {source_ref, options}   -> project (starts job)
POST /api/uploads                          -> upload id;  PATCH /api/uploads/{id} (Content-Range chunks, HEAD for offset)
POST /api/projects/{id}/import-path {path}
GET  /api/projects/{id}  | DELETE (removes all artifacts)
GET  /api/jobs/{id}/events                 SSE (Last-Event-ID resumable)
POST /api/jobs/{id}/cancel | resume
GET  /api/projects/{id}/clips ; PATCH /api/clips/{id} ; POST /api/clips/{id}/recurate|render
GET  /api/files/{project}/{relpath}        Range requests, path-safe
GET  /api/doctor ; PUT /api/settings ; POST /api/ytdlp/update
```

Upload protocol: chunked PATCH with offsets (tus-like semantics, own minimal implementation), written straight to `*.partial` on disk, server streams via `request.stream()`, fsync per chunk, resume by HEAD offset. Chosen over the tus library to avoid a dependency for ~150 lines, and revisited in ADR if it proves fragile.

## 8. Security

Bind 127.0.0.1, strict CORS (same-origin only), per-run token; URL validation http/https only, resolve host and refuse non-public targets for yt-dlp fetches by default (SSRF; override for LAN sources is a setting); filenames sanitised to a whitelist; file serving via project-id + relative-path resolution with `resolve()` containment check and symlink policy (import-by-path symlinks are allowed only under the master file, never followed for serving other paths); subprocesses invoked with argv lists, never `shell=True`; secrets from OS keychain or `.env`, redacted in logs by a logging filter with a test; cookies-from-browser is explicit opt-in; "delete project" removes the tree and index rows and is tested.

## 9. Evaluation

`eval/` holds fixtures manifest, labels, and a harness that runs on cached transcripts/signals. Metrics per Section 13. Results stored under `eval/results/<prompt_version>/<model>/`. CI runs the cheap suite offline against cached LLM responses (record/replay) and only calls the API when explicitly requested.

## 10. Risk register

| # | Risk | Likelihood/Impact | Mitigation |
|---|---|---|---|
| R1 | 8 GB RAM: large-v3 (~3 GB fp16), Chromium, ffmpeg, face model together OOM | High/High | Resource-class scheduler, one heavy stage at a time, memory guard that pauses before spawn, 4-bit or `large-v3-turbo` fallback. |
| R2 | ASR >= 10x realtime for large-v3 on M1 is likely unmet | High/Med | Benchmark large-v3 vs large-v3-turbo vs distil on mlx-whisper; report actual; default chosen by measured WER and speed (ADR-001). |
| R3 | 60 s render in <= 90 s with Chromium captions on M1 | Med/Med | Caption layer only over bands with content; frame-dedupe for static frames; VideoToolbox Fast mode; report actuals. |
| R4 | ffmpeg lacks libass and zimg | Certain/Med | Detect, install full build (ADR-002), gate features otherwise. |
| R5 | 38 GB free disk | High/High | Preflight, streaming pipes, temp cleanup, symlink import, retention settings. |
| R6 | yt-dlp breaks often; YouTube JS runtime requirement | High/Med | Update action, `doctor` check, pinned version + `make update-ytdlp`. |
| R7 | LLM boundary quality is the product | Med/High | Eval harness from Phase 2, prompt versions, judge comparisons. |
| R8 | Diarization needs gated HF model access | Med/Low | Optional; single-speaker fallback works; active-speaker without it uses lip motion only. |
| R9 | Remotion licence for company use | Low/Med | README notes; ASS renderer remains free of it. |
| R10 | Ground truth from published Shorts is laborious | Med/Med | Start with 3 hand-labelled videos in Phase 2; grow. |
| R11 | Fixture licensing | Med/Low | Only CC-BY/public-domain sources, recorded in a manifest with licence and URL. |
| R12 | HDR: no zscale here | Med/Low | Needs full ffmpeg; test with an HDR fixture after ADR-002. |

## 11. Benchmarks planned (each ends in an ADR)

| ADR | Question | Candidates | Metric |
|---|---|---|---|
| 001 | ASR backend/model on M1 8GB | mlx-whisper large-v3 / large-v3-turbo, faster-whisper CPU int8 | WER on fixture, x-realtime, peak RSS, word-onset error vs WhisperX |
| 002 | ffmpeg build strategy (libass, zimg) | Homebrew tap with options, static build, second binary | Filters present, size, install friction |
| 003 | Word alignment | native word timestamps vs WhisperX forced alignment | onset error vs hand labels (+/-50 ms gate) |
| 004 | Render path | filtergraph vs raw-frame pipe | frame accuracy, fps, RSS |
| 005 | Face detect/track | MediaPipe, YOLO-face, SCRFD (InsightFace) + ByteTrack-style | recall, ID switches, fps on M1 |
| 006 | Caption alpha transport | PNG seq / VP9 alpha / band-only / pipe | render time, disk, decode cost |
| 007 | Audio event tagging | YAMNet vs PANNs | laughter recall on labelled clips, speed, licence |
| 008 | Upscaler (optional flag) | Real-ESRGAN-class vs Lanczos | licence, speed, quality delta |
| 009 | Encoder Fast mode | VideoToolbox settings | VMAF vs libx264 CRF17, speed |
