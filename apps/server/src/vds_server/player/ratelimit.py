"""Limiteur à fenêtre glissante en mémoire (par processus)."""

import contextlib
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import NamedTuple

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


class FailureLimiter:
    """Compte les échecs, avec réservation atomique de la tentative.

    `reserve` compte la tentative AVANT le travail (une rafale concurrente ne passe donc pas
    en bloc) et refuse (429) au-delà de `limit` ; `release` rend la place si la tentative n'est
    pas un échec compté (succès, erreur de forme, panne serveur).
    """

    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._fails: dict[str, deque[float]] = {}
        self._next_purge = 0.0

    def reserve(self, key: str) -> "Reservation":
        now = self._clock()
        fails = self._fails.setdefault(key, deque())
        while fails and now - fails[0] >= self._window:
            fails.popleft()
        if len(fails) >= self._limit:
            retry = max(1, int(self._window - (now - fails[0])) + 1)
            raise ApiError("errors.rate_limited", 429, retry_after_s=retry)
        fails.append(now)
        self._purge(now)
        return Reservation(key, now)

    def release(self, res: "Reservation") -> None:
        fails = self._fails.get(res.key)
        if fails is not None:
            with contextlib.suppress(ValueError):
                fails.remove(res.at)

    def _purge(self, now: float) -> None:
        """Purge des clés expirées, au plus une fois par minute (coût borné)."""
        if now < self._next_purge or len(self._fails) <= 1_000:
            return
        self._next_purge = now + 60
        for k in [k for k, v in self._fails.items() if not v or now - v[-1] >= self._window]:
            del self._fails[k]


class Reservation(NamedTuple):
    key: str
    at: float
