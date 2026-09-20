# ADR-011: Clip selection prompt v3, lead-in trimming and a duration prior

**Status:** accepted. Measured on the 6 hand-labelled fixture videos (labels are the assistant's, so treat the numbers as directional; live LLM runs vary by about +/-0.03 recall).

## What changed
- **Prompt v3** (ideas from reading openshorts' selection prompts, MIT, and supoclip's, AGPL, as references only):
  an explicit *opening rule* (the first sentence must be the strongest line; fix missing context by starting earlier, never by cutting the payoff),
  end on the payoff, length follows the idea (most clips 25-55 s), scoring anchors, no two clips making the same point, cover the whole
  transcript and do not stop at one clip on a long video. The hook rule stays v2 (grounded, at most 12 words): a first attempt at "headline, not verbatim"
  made hooks unsupported by the transcript (hook_supported 0.47) and was reverted.
- **Deterministic lead-in trim:** up to two weak opening sentences (four words or fewer, or a short filler-led sentence) are dropped when the clip stays above the minimum
  length and the hook's evidence sentence is kept.
- **Duration prior:** a mild ranking preference for 25-55 s (no penalty inside, easing off outside).

## Result (live, same 6 videos, same models)
| | v1 | v3 |
|---|---|---|
| recall@3 | 0.374 | 0.449 |
| precision@5 | 0.583 | 0.639 |
| bad-hit rate | 0.0 | 0.0 |
| hook supported | 0.53 | 0.90 |
| cost | $0.288 | $0.323 |

Median clip length 42 s (range 25-75 s); 1 of 20 clips still opens on a weak lead-in.

## Caveats
Small labelled set, one labeller, single runs. Recall against hand labels cannot measure "opens on the strongest line"; that was checked by reading the opening sentence of every clip.
