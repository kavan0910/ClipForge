"""Offline regression: replay recorded LLM responses (no API call, no cost) and check the guarantees."""

import tempfile
from pathlib import Path

import pytest

from clipforge.config import Settings
from clipforge.curate.run import PROMPT_VERSION, CurateParams
from clipforge.evalkit.harness import EVAL_DIR, run_case

TAG = f"{PROMPT_VERSION}-balanced"
VIDEOS = ["hannah_cloke_extreme_weather", "flynn_right_to_research"]
pytestmark = pytest.mark.skipif(
    not (EVAL_DIR / "llm_cache" / TAG).exists(), reason="no recorded responses"
)


@pytest.mark.parametrize("vid", VIDEOS)
def test_recorded_curation_keeps_the_hard_guarantees(vid):
    with tempfile.TemporaryDirectory() as w:
        res, clips = run_case(vid, Settings.model_construct(anthropic_api_key=None, max_job_cost_usd=1.0,
                                                            scan_model="claude-haiku-4-5-20251001", curate_model="claude-sonnet-5",
                                                            max_scan_model="", max_curate_model=""),
                              CurateParams(preset="balanced", n_clips=4), Path(w), None, TAG)  # fmt: skip
    assert clips and res.mid_word_cuts == 0
    assert res.starts_on_sentence == 1.0 and res.hard_reject_pass == 1.0 and res.bad_hit_rate == 0.0
    assert res.hook_supported >= 0.6  # hooks are built from what is said; an unsupported one is flagged in the UI
    assert 0 < res.usd < 0.1  # cost recorded from the API usage fields at record time
