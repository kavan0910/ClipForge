"""A protocol-level stand-in for anthropic.Anthropic used ONLY to test plumbing offline.

It reads the sentence IDs and timestamps from the prompt it receives and answers with a
schema-valid, deterministic result, so tests can exercise chunking, caching, repair retries,
cost accounting and post-processing without network access. Live behaviour is covered by the
`live` tests, which use the real API.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx

LINE = re.compile(r"^(S\d{4}) \[([\d:.]+)-([\d:.]+)\]", re.M)


def _secs(t: str) -> float:
    parts = [float(x) for x in t.split(":")]
    return sum(p * 60**i for i, p in enumerate(reversed(parts)))


class FakeMessages:
    def __init__(self, fail_first_json: bool = False, reject_output_config: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_first_json = fail_first_json
        self.reject_output_config = reject_output_config
        self._failed = False

    def create(self, **req: Any) -> Any:
        self.calls.append(req)
        if self.reject_output_config and "output_config" in req:
            resp: Any = httpx.Response(400, request=httpx.Request("POST", "https://x"))
            raise anthropic.BadRequestError(
                "output_config.format is not supported", response=resp, body=None
            )
        text = "\n".join(
            b["text"] for m in req["messages"] for b in m["content"] if b["type"] == "text"
        )
        schema = json.dumps(req.get("output_config") or req["tools"][0]["input_schema"])
        rows = [(i, _secs(a), _secs(b)) for i, a, b in LINE.findall(text)]
        if "candidates" in schema:
            payload: dict[str, Any] = {"candidates": []}
            for k in range(0, max(len(rows) - 12, 1), 14):
                a, b = rows[k], rows[min(k + 11, len(rows) - 1)]
                payload["candidates"].append(
                    {
                        "start_sentence": a[0],
                        "end_sentence": b[0],
                        "strength": 70 + k % 20,
                        "reason": "self-contained",
                    }
                )
        else:
            clips = []
            for k in range(0, max(len(rows) - 12, 1), 16):
                a, b = rows[k], rows[min(k + 11, len(rows) - 1)]
                clips.append({
                    "start_sentence": a[0], "end_sentence": b[0], "title": f"Clip {k}", "hook": "A striking idea",
                    "hook_evidence_start": a[0], "hook_evidence_end": b[0], "summary": "s", "why_it_works": "w",
                    "scores": {"hook": 80, "self_contained": 85, "single_idea": 80, "payoff": 75,
                               "emotion_novelty_utility": 70, "shareability": 65},
                    "overall": 60 + (k * 7) % 30, "emphasis": [], "description": "d",
                    "hashtags": ["idea"], "risk_flags": [],
                })  # fmt: skip
            payload = {"clips": clips}
        out = (
            json.dumps({"clips": [{"bad": 1}]})
            if self.fail_first_json and not self._failed
            else json.dumps(payload)
        )
        self._failed = True
        if "output_config" in req:
            content = [
                SimpleNamespace(
                    type="text", text=out, model_dump=lambda o=out: {"type": "text", "text": o}
                )
            ]
        else:
            content = [
                SimpleNamespace(
                    type="tool_use",
                    input=json.loads(out),
                    model_dump=lambda o=out: {"type": "tool_use", "input": json.loads(o)},
                )
            ]
        usage = SimpleNamespace(
            input_tokens=len(text) // 3,
            output_tokens=len(out) // 3,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return SimpleNamespace(
            content=content,
            usage=usage,
            stop_reason="end_turn",
            model_dump=lambda: {"content": [c.model_dump() for c in content]},
        )


def fake_client(**kw: Any) -> Any:
    return SimpleNamespace(messages=FakeMessages(**kw))
