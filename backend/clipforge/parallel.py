"""Run independent pipeline work side by side. Daemon threads, so a cancelled job can exit at once."""

from __future__ import annotations

import threading
from collections.abc import Callable


class Background[T]:
    def __init__(self, fn: Callable[[], T], name: str = "bg") -> None:
        self._result: T | None = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(fn,), name=name, daemon=True)
        self._thread.start()

    def _run(self, fn: Callable[[], T]) -> None:
        try:
            self._result = fn()
        except BaseException as e:  # re-raised in the waiting thread
            self._error = e

    def result(self) -> T:
        """Wait for the work and return its value, or re-raise what it raised."""
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]

    def done(self) -> bool:
        return not self._thread.is_alive()


class Deferred[T]:
    """Same interface as Background but runs the work when the result is asked for (strictly sequential mode)."""

    def __init__(self, fn: Callable[[], T], name: str = "deferred") -> None:
        self._fn = fn

    def result(self) -> T:
        return self._fn()

    def done(self) -> bool:
        return False
