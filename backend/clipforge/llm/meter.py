"""Cost meter with a hard per-job cap. Thread-safe; every call is recorded to usage.jsonl."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from clipforge.errors import ClipforgeError
from clipforge.llm.pricing import price_for, usd


class CostCapExceeded(ClipforgeError):
    code = "cost_cap"


@dataclass
class UsageRecord:
    t: float
    stage: str
    model: str
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    usd: float


def estimate_tokens(text: str) -> int:
    """Conservative estimate (about 3 characters per token) used only for the pre-call cap check."""
    return int(len(text) / 3.0) + 32


class CostMeter:
    def __init__(
        self,
        cap_usd: float,
        path: Path | None = None,
        on_record: Callable[[UsageRecord, dict], None] | None = None,
    ) -> None:
        self.cap_usd = cap_usd
        self.path = path
        self.on_record = on_record
        self._lock = threading.Lock()
        self.records: list[UsageRecord] = []

    @property
    def spent(self) -> float:
        with self._lock:
            return sum(r.usd for r in self.records)

    def totals(self) -> dict[str, float]:
        with self._lock:
            rs = list(self.records)
        return {
            "input_tokens": sum(r.input_tokens for r in rs),
            "cache_write_tokens": sum(r.cache_write_tokens for r in rs),
            "cache_read_tokens": sum(r.cache_read_tokens for r in rs),
            "output_tokens": sum(r.output_tokens for r in rs),
            "usd": round(sum(r.usd for r in rs), 6),
        }

    def check(self, model: str, est_input_tokens: int, max_output_tokens: int) -> None:
        """Refuse a call whose worst case (all input uncached, all output used) would pass the cap."""
        p = price_for(model)
        worst = (est_input_tokens * p.input + max_output_tokens * p.output) / 1_000_000
        if self.spent + worst > self.cap_usd:
            raise CostCapExceeded(
                f"Stopped before spending more than the ${self.cap_usd:.2f} job cap "
                f"(spent ${self.spent:.4f}; the next call could cost up to ${worst:.4f}).",
                "Raise MAX_JOB_COST_USD, pick the Economy preset, or narrow the clip count.",
            )

    def record(self, stage: str, model: str, usage: object) -> UsageRecord:
        """Record one API response from its `usage` fields."""
        g = lambda name: int(getattr(usage, name, 0) or 0)  # noqa: E731
        rec = UsageRecord(
            t=round(time.time(), 3),
            stage=stage,
            model=model,
            input_tokens=g("input_tokens"),
            cache_write_tokens=g("cache_creation_input_tokens"),
            cache_read_tokens=g("cache_read_input_tokens"),
            output_tokens=g("output_tokens"),
            usd=0.0,
        )
        rec.usd = usd(
            model,
            rec.input_tokens,
            rec.output_tokens,
            rec.cache_write_tokens,
            rec.cache_read_tokens,
        )
        with self._lock:
            self.records.append(rec)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(rec)) + "\n")
        if self.on_record:
            self.on_record(rec, {**self.totals(), "cap_usd": self.cap_usd})
        return rec
