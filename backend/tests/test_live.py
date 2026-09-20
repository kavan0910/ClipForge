"""Live API checks (spend real money: about one cent in total). Run: uv run pytest -m live backend/tests/test_live.py"""

import os

import pytest

from clipforge.config import get_settings
from clipforge.curate.schema import ScanResult
from clipforge.llm.client import Block, LLMClient
from clipforge.llm.meter import CostMeter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not get_settings().anthropic_api_key, reason="no API key"),
]

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5"]
TRANSCRIPT = "\n".join(
    f"S{i:04d} [{i // 60:02d}:{i % 60:02d}.0-{i // 60:02d}:{i % 60:02d}.9] Sentence number {i} talks about topic {i % 7}."
    for i in range(1, 400)
)  # ~6k tokens: above every model's minimum cacheable prefix


def client(tmp_path, cap=0.5):
    key = get_settings().anthropic_api_key
    assert key
    meter = CostMeter(cap, tmp_path / "usage.jsonl")
    return LLMClient(key.get_secret_value(), meter, tmp_path / "run", "live-test"), meter


@pytest.mark.parametrize("model", MODELS)
def test_structured_output_validates_on_every_model(tmp_path, model):
    llm, meter = client(tmp_path)
    out = llm.structured(
        stage="probe", model=model, system="Return candidate clip ranges using only sentence IDs given.",
        blocks=[Block("S0001 [00:00.0-00:05.0] Hello.\nS0002 [00:05.0-00:30.0] A full thought about bulldogs."),
                Block("Return exactly one candidate covering S0001 to S0002.")],
        model_cls=ScanResult, tool_name="report_candidates", max_tokens=400,
    )  # fmt: skip
    assert out.candidates and out.candidates[0].start_sentence == "S0001"
    assert meter.totals()["usd"] > 0


def test_prompt_cache_is_written_then_read_and_meter_uses_usage_fields(tmp_path):
    llm, meter = client(tmp_path)
    model = "claude-haiku-4-5-20251001"
    for _ in range(2):
        llm.structured(
            stage="cache", model=model, system="Return candidate clip ranges using only sentence IDs given.",
            blocks=[Block(TRANSCRIPT, cache=True), Block("Return at most one candidate.")],
            model_cls=ScanResult, tool_name="report_candidates", max_tokens=300,
        )  # fmt: skip
    first, second = meter.records
    assert (
        first.cache_write_tokens > 1000 and second.cache_read_tokens > 1000
    )  # cache hit on the repeat
    assert second.usd < first.usd  # reads are 10x cheaper than writes
    assert os.path.exists(tmp_path / "usage.jsonl")
