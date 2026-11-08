"""Token-bucket rate limiting and 429 backoff."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

import httpx

T = TypeVar("T")


class TokenBucket:
    """`per_minute` requests per minute. burst=1 spaces calls evenly (60/rpm seconds
    apart), which is safest against per-minute windows we can't see."""

    def __init__(self, per_minute: float, burst: int = 1,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.rate = per_minute / 60.0
        self.cap = float(burst)
        self.tokens = float(burst)
        self.clock, self.sleep = clock, sleep
        self.last = clock()

    def acquire(self) -> float:
        """Block until a token is available. Returns seconds waited."""
        now = self.clock()
        self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
        self.last = now
        waited = 0.0
        if self.tokens < 1:
            waited = (1 - self.tokens) / self.rate
            self.sleep(waited)
            self.last = self.clock()
            self.tokens = 1.0
        self.tokens -= 1
        return waited


def backoff_delay(attempt: int, retry_after: float | None = None, cap: float = 60.0) -> float:
    """Exponential (2, 4, 8, ...) unless the server says how long to wait."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after, cap)
    return min(2.0 ** (attempt + 1), cap)


def post_with_backoff(url: str, json: dict, headers: dict, timeout_s: float,
                      retries: int = 4, sleep: Callable[[float], None] = time.sleep) -> httpx.Response:
    """POST, retrying 429/503 with backoff. Returns the last response either way."""
    for attempt in range(retries + 1):
        r = httpx.post(url, json=json, headers=headers, timeout=timeout_s)
        if r.status_code not in (429, 503) or attempt == retries:
            return r
        try:
            ra = float(r.headers.get("retry-after", ""))
        except ValueError:
            ra = None
        sleep(backoff_delay(attempt, ra))
    return r  # unreachable


def retry_call(fn: Callable[[], T], is_retryable: Callable[[Exception], float | None],
               retries: int = 4, sleep: Callable[[float], None] = time.sleep) -> T:
    """Call fn; on an exception for which is_retryable returns a delay (seconds, or 0
    for default backoff), sleep and retry. Other exceptions propagate."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            hint = is_retryable(e)
            if hint is None or attempt == retries:
                raise
            sleep(backoff_delay(attempt, hint or None))
    raise AssertionError("unreachable")


__all__: list[Any] = ["TokenBucket", "backoff_delay", "post_with_backoff", "retry_call"]
