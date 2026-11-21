import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """In-memory limiter. One process only; use Redis or a proxy for multi-node."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, *keys: str) -> float | None:
        """Record one hit on every key. Returns seconds to wait if any key is over the limit."""
        now = self.clock()
        with self._lock:
            wait: float | None = None
            for key in keys:
                hits = self._hits[key]
                while hits and hits[0] <= now - self.window:
                    hits.popleft()
                if len(hits) >= self.limit:
                    wait = max(wait or 0.0, hits[0] + self.window - now)
                else:
                    hits.append(now)
            return wait
