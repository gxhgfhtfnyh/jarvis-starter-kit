"""Thread-safe token-bucket rate limiter.

Logic from hermes-omni (w1:pV); only change: restored the dunder
`__init__` name, which the pane's markdown rendering had stripped to `init`.
"""
import math
import threading
import time


class RateLimiter:
    """Thread-safe token bucket; refill_rate is tokens per second.

    Starts full. Each successful allow() consumes one token.
    """

    def __init__(self, capacity, refill_rate):
        capacity = float(capacity)
        refill_rate = float(refill_rate)

        if not math.isfinite(capacity) or capacity < 1:
            raise ValueError("capacity must be finite and at least 1")
        if not math.isfinite(refill_rate) or refill_rate < 0:
            raise ValueError("refill_rate must be finite and nonnegative")

        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens = capacity
        self._lock = threading.Lock()
        self._last_refill = time.monotonic()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now

            if self._tokens < 1:
                return False

            self._tokens -= 1
            return True
