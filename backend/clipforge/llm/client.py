"""Structured LLM calls: JSON-schema output (or forced tool use), prompt caching, one repair retry.

The client never logs the API key. Every raw response is written next to the run for debugging
and evals. The cost cap is checked before each call from a conservative worst case.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from clipforge.errors import ClipforgeError
from clipforge.llm.meter import CostMeter, estimate_tokens

T = TypeVar("T", bound=BaseModel)

_STRIP = {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern",
          "exclusiveMinimum", "exclusiveMaximum", "default", "title", "format"}  # fmt: skip


class LLMError(ClipforgeError):
    code = "llm"


@dataclass
class Block:
    text: str
    cache: bool = False


def loose_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for the API: no numeric/length constraints (validated locally), closed objects."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def walk(node: Any, in_properties: bool = False) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            # Inside a `properties` mapping the keys are field NAMES (a field called "title" must survive).
            out = {
                k: walk(v, k == "properties")
                for k, v in node.items()
                if in_properties or (k not in _STRIP and k != "$defs")
            }
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return json.loads(fence.group(1) if fence else text)


class LLMClient:
    def __init__(
        self,
        api_key: str,
        meter: CostMeter,
        run_dir: Path,
        prompt_version: str,
        client: Any | None = None,  # anthropic.Anthropic or a record/replay stand-in
    ) -> None:
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.meter, self.run_dir, self.prompt_version = meter, run_dir, prompt_version
        run_dir.mkdir(parents=True, exist_ok=True)
        self._n = 0
        self._lock = threading.Lock()
        self._mode: dict[str, str] = {}  # model -> "json" | "tool"

    def _save_raw(self, stage: str, request: dict[str, Any], response: Any) -> None:
        with self._lock:
            self._n += 1
            n = self._n
        safe = {k: v for k, v in request.items() if k != "messages"}
        payload = {
            "prompt_version": self.prompt_version,
            "stage": stage,
            "request": {**safe, "messages": request["messages"]},
            "response": response.model_dump() if hasattr(response, "model_dump") else str(response),
        }
        (self.run_dir / f"raw_{n:03d}_{stage}.json").write_text(
            json.dumps(payload, indent=1, default=str)
        )

    def _request(
        self, model: str, system: str, messages: list[dict], schema: dict[str, Any], tool_name: str,
        max_tokens: int, mode: str,
    ) -> dict[str, Any]:  # fmt: skip
        req: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "messages": messages,
            "system": [{"type": "text", "text": system}],
        }  # fmt: skip
        if mode == "json":
            req["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
            if not model.startswith("claude-haiku"):
                req["thinking"] = {
                    "type": "disabled"
                }  # adaptive thinking is on by default: extra cost, no gain here
        else:
            req["tools"] = [
                {"name": tool_name, "description": "Return the result.", "input_schema": schema}
            ]
            req["tool_choice"] = {"type": "tool", "name": tool_name}
            req["thinking"] = {"type": "disabled"}  # thinking cannot be combined with forced tools
        return req

    def _call(self, stage: str, model: str, req: dict[str, Any]) -> anthropic.types.Message:
        est = estimate_tokens(json.dumps(req["messages"]) + json.dumps(req["system"]))
        self.meter.check(model, est, req["max_tokens"])
        try:
            resp = self.client.messages.create(**req)
        except anthropic.APIStatusError as e:
            self._save_raw(stage + "_error", req, {"error": str(e)})
            raise
        self.meter.record(stage, model, resp.usage)
        self._save_raw(stage, req, resp)
        return resp

    @staticmethod
    def _payload(resp: anthropic.types.Message, mode: str) -> Any:
        if resp.stop_reason == "max_tokens":
            raise ValueError("The response was cut off (max_tokens).")
        if resp.stop_reason == "refusal":
            raise LLMError(
                "The model declined this request.", "Try again or change the steering text."
            )
        if mode == "tool":
            for b in resp.content:
                if b.type == "tool_use":
                    return b.input
            raise ValueError("The model returned no tool call.")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return _extract_json(text)

    def structured(
        self, *, stage: str, model: str, system: str, blocks: list[Block], model_cls: type[T],
        tool_name: str, max_tokens: int = 4096,
    ) -> T:  # fmt: skip
        """One structured call with a single repair retry on invalid output."""
        schema = loose_schema(model_cls)
        content: list[dict[str, Any]] = []
        for b in blocks:
            item: dict[str, Any] = {"type": "text", "text": b.text}
            if b.cache:
                item["cache_control"] = {"type": "ephemeral"}
            content.append(item)
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        mode = self._mode.get(model, "json")
        last_err = ""
        for attempt in range(2):
            req = self._request(model, system, messages, schema, tool_name, max_tokens, mode)
            try:
                resp = self._call(stage if attempt == 0 else stage + "_repair", model, req)
            except anthropic.BadRequestError as e:
                msg = str(e).lower()
                if "credit balance" in msg:
                    raise LLMError(
                        "Your Anthropic account has no credit left.",
                        "Add credits under Plans & Billing at console.anthropic.com, then retry.",
                    ) from e
                if mode == "json" and any(
                    k in msg for k in ("output_config", "format", "schema", "structured")
                ):
                    mode = self._mode[model] = "tool"
                    req = self._request(
                        model, system, messages, schema, tool_name, max_tokens, mode
                    )
                    resp = self._call(stage, model, req)
                else:
                    raise LLMError(
                        f"The API rejected the request: {e}", "Check the model id in your settings."
                    ) from e
            except anthropic.AuthenticationError as e:
                raise LLMError(
                    "The Anthropic API key was rejected.", "Check ANTHROPIC_API_KEY."
                ) from e
            try:
                return model_cls.model_validate(self._payload(resp, mode))
            except (ValidationError, ValueError) as e:
                last_err = str(e)[:1500]
                raw = resp.content[0].model_dump() if resp.content else {}
                messages = [
                    *messages,
                    {"role": "assistant", "content": [{"type": "text", "text": json.dumps(raw)[:6000]}]},
                    {"role": "user", "content": [{"type": "text", "text": (
                        "Your previous output was invalid: " + last_err
                        + "\nReturn a corrected result that satisfies every constraint.")}]},
                ]  # fmt: skip
        raise LLMError("The model returned invalid output twice.", "Details: " + last_err[:300])
