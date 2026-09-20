# Clipforge

Local-first AI clipping studio: one long video in, ready-to-post vertical shorts out.
Your video never leaves this machine. Only transcript text and light metadata go to the
Anthropic API for the editorial step (Phase 2), and the UI says so.

**Status: Phase 1 of 6 (ingest and transcript).** See `docs/PLAN.md` and `docs/checkpoints/`.

## Setup

```
make setup      # installs uv, deno and ffmpeg-full (Homebrew), Python deps, web + captions deps, builds the UI
cp .env.example .env   # then add ANTHROPIC_API_KEY (needed from Phase 2)
make doctor     # checks hardware, ffmpeg, yt-dlp + JS runtime, keys, disk
make start      # UI + API on http://127.0.0.1:8765 (localhost only)
```

CLI: `uv run clipforge run <url|path>` ingests and transcribes; `clipforge doctor` diagnoses.

## What works today

- Two equal inputs: paste a link (any yt-dlp site; YouTube is the primary target) or upload a file
  (chunked, resumable, streamed to disk; or import by path with no copy). Both become the same `Source`.
- ffprobe validation (rejects non-media, corrupt files, no audio), quality report, rotation/VFR/multi-audio
  handling, 16 kHz WAV and a 720p proxy.
- Word-level transcription (mlx-whisper on Apple Silicon, faster-whisper elsewhere), chunked with overlap,
  resumable, with hallucination guards (Whisper scores, VAD, silence, repetition, stock phrases).
- Live progress over SSE, cancel (kills the whole process tree) and resume.

## Hardware notes

Target: Apple Silicon (M-series) with 16 GB. See `docs/decisions/ADR-001-asr-backend-and-model.md` for
measured ASR speeds. Default ASR model is `large-v3-turbo`; set `ASR_MODEL=large-v3` for maximum accuracy.

## Costs

No API calls happen in Phase 1. Curation cost is estimated at about $0.15 per hour of video in Balanced mode
and will be measured against the API `usage` fields in Phase 2.

## Licences and terms

- Remotion (captions, Phase 4) is free for individuals and small companies; larger for-profit companies need
  a company licence. See `captions/README.md`.
- Models: Whisper (MIT), Silero VAD (MIT), pyannote community-1 (CC-BY-4.0, gated: accept the terms on Hugging Face).
- **Downloading:** only process videos you own or have permission to reuse. Downloading may violate a site's
  terms of service. Clipforge is a tool for your own content.

## Known gaps

- Speaker diarization needs your Hugging Face account to accept the pyannote terms; until then it degrades to
  single-speaker mode with a visible warning.
- No SQLite index yet: the filesystem manifests are the source of truth; the index arrives with clips in Phase 2.
- Settings screen (cookies-from-browser, brand kits) arrives in Phase 5; today use `.env`.
- Live-chat replay download is not implemented yet (the flag is harvested; rate extraction is Phase 2).
