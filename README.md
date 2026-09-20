# Clipforge

Local-first AI clipping studio: one long video in, ready-to-post vertical shorts out.
Your video never leaves this machine. Only transcript text and light metadata go to the
Anthropic API for the editorial step (Phase 2), and the UI says so.

**Status: Phase 5 of 6 done (review UI, render queue, export, settings). Phase 6 (publishing, audiogram, final report) next.** See `docs/PLAN.md` and `docs/checkpoints/`.

## Setup

```
make setup      # installs uv, deno and ffmpeg-full (Homebrew), Python deps, web + captions deps, builds the UI
cp .env.example .env   # then add ANTHROPIC_API_KEY (needed from Phase 2)
make doctor     # checks hardware, ffmpeg, yt-dlp + JS runtime, keys, disk
make start      # UI + API on http://127.0.0.1:8765 (localhost only)
```

CLI: `clipforge run <url|path> --clips 8 --duration 30-60 --preset balanced --steer "..."`, `clipforge curate <project> --mode shorter`,
`clipforge render <project> --clip c001 --template karaoke-pop --brand acme [--renderer ass] [--fast] [--debug]`, `clipforge templates`, `clipforge brand create`, `clipforge eval core [--live]`, `clipforge doctor`.

## What works today

- Two equal inputs: paste a link (any yt-dlp site; YouTube is the primary target) or upload a file
  (chunked, resumable, streamed to disk; or import by path with no copy). Both become the same `Source`.
- ffprobe validation (rejects non-media, corrupt files, no audio), quality report, rotation/VFR/multi-audio
  handling, 16 kHz WAV and a 720p proxy.
- Word-level transcription (mlx-whisper on Apple Silicon, faster-whisper elsewhere), chunked with overlap,
  resumable, with hallucination guards (Whisper scores, VAD, silence, repetition, stock phrases).
- Signals (energy, speech rate, laughter/applause, shot changes, motion, platform heatmap) and two-stage LLM curation
  (cheap scan, stronger curator) with deterministic boundary snapping, a live cost meter and a hard cost cap.
  Clip score is labelled "heuristic", not a virality prediction.
- `clipforge render`: frame-accurate cuts through an EDL, speaker-aware reframing to 1080x1920 (single, two-shot,
  stacked, screen+cam, blurred fit), filler/pause cleanup, click-free audio at -14 LUFS, one H.264 encode, debug render.
- Designer captions: 8 templates, word-by-word highlight, hook overlay, brand kits (logo, intro/outro cards), rendered by Remotion
  (default) or libass (fast fallback), composited inside the single encode; SRT/VTT/ASS sidecars, thumbnail and `metadata.json`.
- Review UI (`#/p/<id>/c/<clip>`): video preview with the exact caption composition overlaid, transcript with click-to-seek, text-based
  cutting (select words, X), draggable trim, filler restore, per-segment layout override, live template switch, hook/title/hashtag editing,
  autosave. Keyboard: Space play, J/K/L, `[` `]` in/out, A/R approve/reject, X/U cut/restore, arrows step by word.
- Render queue with cancel and download; export as MP4 folder/zip, OpenTimelineIO, FCPXML, FCP7 XML or CMX 3600 EDL (cuts as edits on the source).
- Settings: keys (masked, written to `.env` mode 600), preferences, health check, storage cleanup, brand kits.
- Live progress over SSE, cancel (kills the whole process tree) and resume.

## Hardware notes

Target: Apple Silicon (M-series) with 16 GB. See `docs/decisions/ADR-001-asr-backend-and-model.md` for
measured ASR speeds. Default ASR model is `large-v3-turbo`; set `ASR_MODEL=large-v3` for maximum accuracy.

## Fonts and licences
Bundled fonts are SIL OFL 1.1 (`captions/fonts/LICENSES.md`); run `uv run python scripts/fetch_fonts.py` once. Remotion is free for individuals and
small teams; larger companies need a licence (`captions/README.md`).

## Costs

Curation is the only step that calls the API (transcript text only). Its cost is estimated at about $0.15 per hour of video in Balanced mode
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
- The LLM path is verified offline only until the API key has credit (see `docs/checkpoints/phase-2.md`).
- Active speaker is only verified on synthetic multi-person scenes; see `docs/checkpoints/phase-3.md`.
- No SQLite index yet: the filesystem manifests are the source of truth; the index arrives with clips in Phase 2.
- FCPXML cannot express NTSC frame rates in the bundled adapter: exports fall back to an integer rate and say so.
- The video preview is a caption-free proxy from the last render; after edits the captions update live, the picture after a re-render.
- Live-chat replay download is not implemented yet (the flag is harvested; rate extraction is Phase 2).
