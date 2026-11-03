"""Cache des `feed_id` autorisés par écran (route `GET /player/feeds/{id}`).

Évite de reconstruire toute la programmation à chaque lecture de flux. Alimenté à chaque synchro
de programmation, invalidé à la publication (bus) et borné (TTL court, nombre d'entrées).
"""

import time
from collections import OrderedDict
from collections.abc import Iterable
from uuid import UUID

from vds_sdk import Event

TTL_S = 60.0
MAX_ENTRIES = 2000


class FeedAccessCache:
    def __init__(self, ttl_s: float = TTL_S, limit: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_s
        self._limit = limit
        self._data: OrderedDict[UUID, tuple[float, frozenset[str]]] = OrderedDict()

    def get(self, screen_id: UUID) -> frozenset[str] | None:
        hit = self._data.get(screen_id)
        if hit is None:
            return None
        if time.monotonic() - hit[0] > self._ttl:
            del self._data[screen_id]
            return None
        return hit[1]

    def put(self, screen_id: UUID, feed_ids: Iterable[str]) -> None:
        self._data[screen_id] = (time.monotonic(), frozenset(feed_ids))
        self._data.move_to_end(screen_id)
        while len(self._data) > self._limit:
            self._data.popitem(last=False)

    def invalidate(self, screen_id: UUID | None = None) -> None:
        if screen_id is None:
            self._data.clear()
        else:
            self._data.pop(screen_id, None)

    async def on_event(self, _event: Event) -> None:
        self.invalidate()


def feed_ids_of(body: dict[str, object]) -> set[str]:
    """Identifiants de flux référencés par une programmation résolue."""
    playlists = body.get("playlists") or {}
    out: set[str] = set()
    if isinstance(playlists, dict):
        for pl in playlists.values():
            for item in pl.get("items", []):
                out.update(item.get("feeds", []))
    return out
