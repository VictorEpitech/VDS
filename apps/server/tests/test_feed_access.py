"""Cache des flux autorisés par écran : TTL, bornes, invalidation."""

from uuid import uuid4

import pytest
from vds_server.feeds import access
from vds_server.feeds.access import FeedAccessCache, feed_ids_of


def test_feed_ids_of_collects_all_playlists() -> None:
    body = {
        "playlists": {
            "pl_a": {"items": [{"feeds": ["a" * 32]}, {"id": "x"}]},
            "pl_b": {"items": [{"feeds": ["b" * 32, "a" * 32]}]},
        }
    }
    assert feed_ids_of(body) == {"a" * 32, "b" * 32}
    assert feed_ids_of({}) == set()


def test_put_get_invalidate_and_bound() -> None:
    cache = FeedAccessCache(ttl_s=60, limit=2)
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    cache.put(s1, ["a"])
    cache.put(s2, ["b"])
    assert cache.get(s1) == frozenset({"a"})
    cache.put(s3, [])
    assert cache.get(s1) is None and cache.get(s2) == frozenset({"b"})  # borné à 2
    cache.invalidate(s3)
    assert cache.get(s3) is None
    cache.invalidate()
    assert cache.get(s1) is None


def test_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(access.time, "monotonic", lambda: now[0])
    cache = FeedAccessCache(ttl_s=60)
    sid = uuid4()
    cache.put(sid, ["a"])
    now[0] += 59
    assert cache.get(sid) == frozenset({"a"})
    now[0] += 2
    assert cache.get(sid) is None
