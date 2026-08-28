from collections import defaultdict, deque
from time import monotonic


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = monotonic()
        requests = self._requests[key]
        while requests and requests[0] <= now - self.window_seconds:
            requests.popleft()
        if len(requests) >= self.limit:
            return False
        requests.append(now)
        return True

    def clear(self) -> None:
        self._requests.clear()
