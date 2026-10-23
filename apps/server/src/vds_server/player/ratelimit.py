"""Limiteur à fenêtre glissante en mémoire (par processus)."""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from vds_server.core.errors import ApiError


class RateLimiter:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Compte un appel ; 429 `errors.rate_limited` au-delà de la limite."""
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            retry = max(1, int(self._window - (now - hits[0])) + 1)
            raise ApiError("errors.rate_limited", 429, retry_after_s=retry)
        hits.append(now)
        if len(self._hits) > 10_000:  # borne mémoire : purge des clés vides
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self._window]:
                del self._hits[k]
