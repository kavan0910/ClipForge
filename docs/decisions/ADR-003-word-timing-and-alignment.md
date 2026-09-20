# ADR-003: word timing accuracy and forced alignment

- Status: accepted; **the +/-50 ms requirement is NOT met** (stated plainly below)
- Date: 2026-09-20

## Question
Section 12 wants caption word onsets within +/-50 ms of the audio. Whisper word timestamps are cross-attention
guesses, so I measured them, then tried forced alignment.

## Independent reference
`clipforge.captions.onsets.detect_onset`: for words after a >= 360 ms pause, find the 50% rise between the quiet
pause floor and the word's body level. The detector itself is validated on synthetic speech bursts with known
onsets (found within 20 ms; `test_onsets.py`). 232 clean onsets across the real fixtures.

## Results (real footage, M1)
| | within +/-50 ms | median abs error | p90 |
|---|---|---|---|
| Whisper large-v3-turbo word starts | **27%** | 100 ms | 180 ms |
| + best possible global shift (-70 ms) | 31% | 80 ms | - |
| torchaudio MMS_FA forced alignment (paired, same 182 onsets) | 24% | **160 ms** | 461 ms |

The bias differs per video (median signed error -110 ms to +50 ms), so no global correction exists. Forced alignment
was **worse**: CTC aligners place a word start at its first character spike, which lags the acoustic onset by 100+
ms. (`scripts/measure_alignment.py`; the aligner is kept as an experimental module but not used.)

## Decision
Keep Whisper timings. Two mitigations, both measured only against the same reference, so their gain is partly
circular and is not claimed as accuracy: (1) words that follow a pause and have a clean acoustic onset within 120 ms
are snapped to it; (2) captions run 50 ms early (`CAPTION_LEAD`), because captions read better slightly early than late.

## Consequence
Word-by-word highlight timing is typically within about 100 ms, not 50 ms. Phrase starts after pauses are better
than the mid-phrase words. Meeting +/-50 ms would need a phoneme-level aligner evaluated against hand-labelled onsets.
