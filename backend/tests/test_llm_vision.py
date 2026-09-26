"""Image content blocks: base64 payloads must never leak into the character-count cost
estimate (that would make a cheap vision call look like it costs hundreds of thousands of
tokens and trip the job's cost cap for no reason)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from pydantic import BaseModel

from clipforge.llm.client import Block, LLMClient, _content_item, _estimate_message_tokens
from clipforge.llm.meter import CostMeter


class Result(BaseModel):
    seen: bool


def test_content_item_builds_an_image_block():
    data = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    item = _content_item(Block(image=data, image_media_type="image/jpeg"))
    assert item["type"] == "image"
    assert item["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": base64.b64encode(data).decode(),
    }


def test_content_item_builds_a_text_block_with_cache():
    item = _content_item(Block(text="hello", cache=True))
    assert item == {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}


def test_estimate_treats_images_as_a_flat_ceiling_not_their_base64_length():
    big_image = b"x" * 200_000  # stands in for a real thumbnail's raw bytes
    messages = [
        {
            "role": "user",
            "content": [_content_item(Block(text="hi")), _content_item(Block(image=big_image))],
        }
    ]
    est = _estimate_message_tokens(messages)
    assert est < 3000  # a naive char-count estimate of the base64 payload would be > 250,000


def test_structured_call_with_an_image_does_not_trip_a_small_cost_cap(tmp_path):
    resp = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps({"seen": True}))],
        usage=SimpleNamespace(
            input_tokens=50,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: resp))
    # A raw-base64-length estimate of a 200 KB image would put the worst case near $0.09;
    # the fixed estimate keeps it near $0.002, so this tiny cap only survives with the fix.
    meter = CostMeter(cap_usd=0.005)
    llm = LLMClient("key", meter, tmp_path, "v1", client=fake)
    out = llm.structured(
        stage="character_scan", model="claude-haiku-4-5-20251001", system="s",
        blocks=[Block(text="Is the character visible?"), Block(image=b"x" * 200_000)],
        model_cls=Result, tool_name="report", max_tokens=64,
    )  # fmt: skip
    assert out.seen is True
