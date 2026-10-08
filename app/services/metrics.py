import threading
from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self._c: Counter[str] = Counter()
        self._lock = threading.Lock()

    def incr(self, name: str) -> None:
        with self._lock:
            self._c[name] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._c)
