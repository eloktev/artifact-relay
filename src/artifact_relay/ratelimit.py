"""Bounded in-process login throttling.

A fixed window per client address. This is deliberately not distributed: the service runs as
a single replica, and an in-process limiter cannot be knocked out by losing an external
store. The key table is capped and evicted least-recently-used first, so a spoofed-address
flood costs a fixed amount of memory rather than an unbounded one.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

DEFAULT_MAX_KEYS = 4096


@dataclass
class _Window:
    started_at: float
    failures: int


class FixedWindowRateLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._windows: OrderedDict[str, _Window] = OrderedDict()

    def __len__(self) -> int:
        return len(self._windows)

    def _current(self, key: str, now: float) -> _Window | None:
        window = self._windows.get(key)
        if window is None:
            return None
        if now - window.started_at >= self.window_seconds:
            del self._windows[key]
            return None
        self._windows.move_to_end(key)
        return window

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds the caller must wait, or ``0`` when another attempt is allowed."""
        moment = time.monotonic() if now is None else now
        window = self._current(key, moment)
        if window is None or window.failures < self.max_attempts:
            return 0
        return max(1, int(window.started_at + self.window_seconds - moment) + 1)

    def register_failure(self, key: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        window = self._current(key, moment)
        if window is None:
            self._windows[key] = _Window(started_at=moment, failures=1)
            self._windows.move_to_end(key)
        else:
            window.failures += 1
        while len(self._windows) > self.max_keys:
            self._windows.popitem(last=False)

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)
