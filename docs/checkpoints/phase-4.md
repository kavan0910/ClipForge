# Phase 4 checkpoint: captions and packaging

**Status: built and measured. One Section 12 caption criterion is NOT met (word onsets within +/-50 ms; see ADR-003).**

## Acceptance (Captions row of Section 12)
| Requirement | Result |
|---|---|
| Max 2 lines | Enforced in chunking and verified on **every** exported still of real clips (`max_lines 2`); property-style tests over all 8 templates x plain / long-word text |
| Inside safe zones | Bottom 20%, top 8%, right 14% (caption area) kept clear. libass render of 8 templates x {plain, long words, CJK, Arabic} = 32 cases inside; every exported Remotion still of 2 real clips inside (caption band x 316-426 stills, hook layer) |
| No overflow with long words | A 44-character word shrinks to the exact fit (down to 30%) instead of overflowing; found by a test that failed first |
| CJK and RTL | Fallback fonts (Noto Sans SC / Noto Sans Arabic) selected per chunk. Verified visually in Remotion (Arabic shaped, right-to-left, highlight correct) and by libass safe-zone tests. Arabic needs a 15% width margin because libass renders it wider than PIL measures |
| Readable on light and dark backgrounds | WCAG contrast of fill vs its surrounding ring >= 3:1 on both white and black for all 8 templates, measured on rendered pixels. Neon glow failed at 1.74:1 and was redesigned (navy outline under the glow) |
| **Word onsets within +/-50 ms** | **NOT MET.** Whisper starts: 27% within 50 ms, median error 100 ms (232 clean onsets, 6 videos). Forced alignment (MMS_FA) was worse (median 160 ms). Mitigations shipped: snap post-pause phrase starts to the acoustic onset, 50 ms caption lead. See ADR-003 |
| Preview and export visually match | Playwright compares the in-app `@remotion/player` against the exported layer still: **0.0000 mismatch** over 31k-69k caption pixels on 3 frames (proves non-empty ink first) |

## What was built
Template spec (JSON design tokens + exported JSON Schema) shared by both renderers; 8 templates (karaoke pop, clean minimal,
boxed highlight, heavy outline, typewriter, classic subtitle, neon glow, editorial serif); chunker with rate-aware chunk size,
pause and punctuation breaks, real-font line fitting, emphasis words, profanity masking, punctuation stripping, capped emoji;
ASS renderer (draft/fallback) and Remotion renderer (default) behind one timeline; hook overlay with its own style; SRT/VTT/ASS
sidecars; brand kits (colours, logo watermark, intro/outro cards, default template, vocabulary) with a CLI; thumbnails (best of 16
frames by sharpness, face, open eyes; title placed clear of the face); `metadata.json` (scores, source timestamps, EDL, models,
prompt version, cost); in-app caption preview page; bundled OFL fonts with a fetch script and licence manifest.
Captions are composited **inside the single video encode** (ASS filter, or alpha PNG stills overlaid): the picture is compressed once.

## Measured
- 27.8 s clip: 40 s with ASS captions, 46 s with Remotion captions (M1). Caption layer: 316 distinct stills for a 28 s clip.
- Remotion transport benchmark (ADR-006): VP9-alpha encode was the slow part (97 s for 10 s); unique-state stills ~10 s.
- Branded clip (intro + outro + watermark): 41.73 s = 38.56 + 3.2; A/V drift 0.08 frame; -14.2 LUFS; face in crop 98.7%.

## Bugs found by real runs
Remotion collapsed spaces between words (inline-block whitespace); libass font size means line height, not em, so libass text was
0.7-1.3x the width PIL measured; the pop/scale tags reset the shrink-to-fit factor; `prepare()` ignored the intro offset and never
received the audio path (two edits silently missed the reformatted code, found by inspecting a frame); a facecam heuristic and
the OpenCV 5 change (no Haar cascades) required an eye-landmark contrast method for "open eyes"; the UI had no "no clips" state.

## Known gaps
- ASS pills/glow are approximations (thick outline, blur); Remotion is the faithful renderer.
- Emoji come from a small keyword table, not the LLM (needs a schema change and another prompt version).
- "Open eyes" is a texture-contrast heuristic on eye landmarks, not a trained expression model; not validated against labels.
- Word-level onset accuracy (above). Fractional frame rates use the 30 fps caption layer (overlay picks the nearest still, <= 1/60 s error).
- No real footage with a facecam or multiple speakers has been captioned; brand kit tested with a generated logo.
- Remotion needs Node 20+ and downloads a Chromium headless shell (~90 MB) on first use; company use may need a Remotion licence.
