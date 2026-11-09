"""Failed-login throttling (in-process; per API instance). Blocks a (client ip, username) pair after too many
failures inside a sliding window. Successful login clears the counter."""
import threading
import time
from collections import defaultdict, deque


class FailureLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures, self.window = max_failures, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def retry_after(self, key: str) -> int:
        """Seconds until the key may try again (0 = allowed)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            self._prune(q, now)
            if len(q) < self.max_failures:
                if not q:
                    self._hits.pop(key, None)
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._prune(q, now)
            q.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_limiter: FailureLimiter | None = None


def login_limiter() -> FailureLimiter:
    global _limiter
    if _limiter is None:
        from app.core.config import get_settings
        s = get_settings()
        _limiter = FailureLimiter(s.login_max_failures, s.login_window_seconds)
    return _limiter


def reset_login_limiter() -> None:
    global _limiter
    _limiter = None
