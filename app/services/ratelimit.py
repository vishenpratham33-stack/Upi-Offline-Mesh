import threading
import time


class TokenBucketLimiter:
    """Per-key token bucket (used per bridge node id)."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self._rate = per_minute / 60.0
        self._cap = float(per_minute)
        self._clock = clock
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._state.get(key, (self._cap, now))
            tokens = min(self._cap, tokens + (now - last) * self._rate)
            if tokens < 1:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - 1, now)
            return True
