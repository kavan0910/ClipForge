# ADR-013: Character-edit mode (find a named character by vision, cut a montage)

**Status:** accepted.

## Context
A second project mode, alongside the transcript-driven "clips" pipeline: upload a full episode or
movie, name a character (e.g. "Gojo"), and get a short vertical edit of their scenes, styled like
the fan-edit genre (colour grade, a slow push-in per shot, cut to a montage) — no dialogue analysis
involved, since the task is entirely visual.

## Decision
- **Finding the character is vision, not transcript.** The existing shot-boundary signal
  (PySceneDetect, already computed for the transcript pipeline's scene-cut signal) segments the
  source into shots; one mid-frame thumbnail per shot is batched (20 per call) into Claude vision
  calls asking whether the named character is on screen, with optional reference images the user
  supplies. This mirrors the transcript pipeline's cheap-scan shape, over frames instead of
  sentences (`character/scan.py`).
- **`LLMClient.Block` gained an `image` field.** The pre-call cost-cap estimate now walks message
  content structurally and prices an image at a flat conservative ceiling (1600 tokens, Anthropic's
  documented max) instead of character-counting its base64 payload — that would have overestimated
  a cheap vision call by 100x and tripped the job's cost cap for no reason.
- **A character edit is still a `Clip`.** `Clip` gained an optional `segments: list[(start, end)]`
  field: several disjoint source ranges cut together, instead of one continuous trimmed span. This
  is the only schema change needed to make every existing downstream feature work unmodified —
  approve/reject, auto-render-top (ADR-012), auto-package, the ready-to-post banner, YouTube
  publish, reveal-in-Finder. `character/edit.py` assembles the matched, selected scenes into one.
- **A separate render path**, not the transcript pipeline's per-frame Python compositor (which is
  built entirely around word-driven layout solving). `character/render.py` builds each shot with
  plain ffmpeg filter graphs — a crop-based push-in (driven by `t`, not `zoompan`'s per-frame state,
  which is known to stutter on continuous video), the same vertical "fit" wash the transcript
  pipeline uses, and one of a few `curves`/`eq` colour grades — then concatenates shots and runs the
  same two-pass loudness normalisation (-14 LUFS) as the transcript path.
- **No transcript/ASR stage runs at all** in this mode (`character/pipeline.py`, branched in
  `jobrunner.py` on `options.mode == "character_edit"`): irrelevant to finding a character on
  screen, and skipping it saves real time on long episodes.
- **The editor doesn't apply.** `ClipEditor`'s trim/caption UI needs a transcript to build its EDL
  preview; a character edit has none, so its clip card hides "Edit & render", "More like this" and
  the six podcast-scoring dimensions, and a new `PUT .../clips/{id}/status` endpoint lets the card
  approve/reject directly without a round trip through the (transcript-dependent) editor GET route.
  Re-curate is refused server-side for this mode for the same reason.

## Scope cut for v1
- One source file per character-edit project (no multi-episode batching into one edit yet).
- One assembled edit per run, not several length/style variants to choose between.
- No music track or beat-sync yet — the edit keeps each shot's original audio. Bring-your-own-audio
  and beat-synced cuts are natural next steps, not built here.
- One fixed grade/push-in style per run (`character/render.py`'s `DEFAULT_GRADE`); no style picker
  in the UI yet, though the render engine already supports selecting one.

## Verification
`test_llm_vision.py` (image content blocks, the cost-estimate fix), `test_character_scan.py` (shot
merging/selection pure logic, batching, an unreadable frame is defensively never counted as a match
regardless of what the model says), `test_character_render.py` (the actual ffmpeg filter graphs —
push-in, vertical fit, grade, concat, loudness — against a real synthetic source, not mocked), and
`test_api_character_edit.py` (validation, reference-image storage size limits, mode surfaced on the
project, re-curate refused). Not yet run against a real episode; that needs the user's own footage.
