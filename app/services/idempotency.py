"""Atomic "claim this hash once" stores. Equivalent to Redis `SET key NX EX ttl`."""
import threading
import time
from collections.abc import Callable
from typing import Protocol


class IdempotencyStore(Protocol):
    def claim(self, key: str) -> bool: ...
    def release(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def evict_expired(self) -> int: ...
    def __len__(self) -> int: ...


class InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, key: str) -> bool:
        """True for exactly one caller per key per TTL window, however many race."""
        now = self._clock()
        with self._lock:
            expiry = self._seen.get(key)
            if expiry is not None and expiry > now:
                return False
            self._seen[key] = now + self._ttl
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._seen.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def evict_expired(self) -> int:
        now = self._clock()
        with self._lock:
            dead = [k for k, exp in self._seen.items() if exp <= now]
            for k in dead:
                del self._seen[k]
            return len(dead)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


class RedisIdempotencyStore:
    """Drop-in distributed implementation for multi-replica deployments (`pip install redis`)."""

    PREFIX = "upimesh:idem:"

    def __init__(self, url: str, ttl_seconds: int):
        import redis

        self._r = redis.Redis.from_url(url)
        self._ttl = ttl_seconds

    def claim(self, key: str) -> bool:
        return bool(self._r.set(self.PREFIX + key, 1, nx=True, ex=self._ttl))

    def release(self, key: str) -> None:
        self._r.delete(self.PREFIX + key)

    def clear(self) -> None:
        for k in self._r.scan_iter(self.PREFIX + "*"):
            self._r.delete(k)

    def evict_expired(self) -> int:
        return 0  # Redis expires keys itself

    def __len__(self) -> int:
        return sum(1 for _ in self._r.scan_iter(self.PREFIX + "*"))
