"""Record/replay wrapper around anthropic.Anthropic so evals run offline on recorded responses."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class CacheMiss(RuntimeError):
    pass


def request_key(req: dict[str, Any]) -> str:
    blob = json.dumps(req, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _rebuild(payload: dict[str, Any]) -> Any:
    """A response object with the attributes LLMClient reads, from recorded JSON."""
    content = []
    for b in payload["content"]:
        ns = SimpleNamespace(
            **{k: v for k, v in b.items() if k in ("type", "text", "input", "name", "id")}
        )
        ns.model_dump = lambda b=b: b
        content.append(ns)
    usage = SimpleNamespace(**payload["usage"])
    return SimpleNamespace(
        content=content, usage=usage, stop_reason=payload.get("stop_reason", "end_turn"),
        model_dump=lambda: payload,
    )  # fmt: skip


class _Messages:
    def __init__(self, cache_dir: Path, real: Any | None) -> None:
        self.cache_dir, self.real = cache_dir, real
        self.hits = self.misses = 0

    def create(self, **req: Any) -> Any:
        key = request_key(req)
        f = self.cache_dir / f"{key}.json"
        if f.exists():
            self.hits += 1
            return _rebuild(json.loads(f.read_text()))
        if self.real is None:
            raise CacheMiss(
                f"No recorded response for this request ({key}). Re-run with --live and an API key "
                "to record it (this spends money)."
            )
        self.misses += 1
        resp = self.real.messages.create(**req)
        payload = {
            "content": [b.model_dump() for b in resp.content],
            "usage": resp.usage.model_dump()
            if hasattr(resp.usage, "model_dump")
            else dict(vars(resp.usage)),
            "stop_reason": resp.stop_reason,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(payload, default=str))
        return _rebuild(payload)


class RecordReplayClient:
    def __init__(self, cache_dir: Path, real: Any | None = None) -> None:
        self.messages = _Messages(cache_dir, real)
