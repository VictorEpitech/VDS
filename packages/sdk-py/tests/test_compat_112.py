"""Contrat vds-core 1.12.0 : flux de données (additif sur 1.11)."""

from typing import Any

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_11,
    CORE_EVENT_PAYLOAD_1_12,
    CORE_EVENTS_1_11,
    CORE_EVENTS_1_12,
    ModuleManifest,
    parse_event,
    permissions,
)
from vds_sdk.events import CRITICAL_EVENTS_1_11, CRITICAL_EVENTS_1_12
from vds_sdk.server import FeedRequest, FeedsApi, SafeHttp, SafeHttpError
from vds_sdk.server.content import ContentType, PlayerItemSpec
from vds_sdk.server.feeds import compute_feed_id

NEW = {"feed.failed", "feed.recovered"}


def manifest(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "content.weather",
        "version": "0.1.0",
        "core": ">=1.12 <2.0",
        "kind": "content-type",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["server", "player", "admin"],
        "entrypoints": {"server": "m:W"},
        "capabilities": ["content.weather"],
        "permissions": ["net.outbound", "content.feeds"],
        "content": {
            "item_schema": "item.schema.json",
            "player": {"element": "vds-content-weather", "script": "assets/player.js"},
            "requires_network": False,
        },
        "author": "V-Tech",
    }
    raw.update(over)
    return raw


def test_version_and_events() -> None:
    assert CORE_API_VERSION == "1.12.0"
    assert NEW == CORE_EVENTS_1_12 - CORE_EVENTS_1_11
    assert CORE_EVENT_PAYLOAD_1_11.items() <= CORE_EVENT_PAYLOAD_1_12.items()
    assert CRITICAL_EVENTS_1_12 == CRITICAL_EVENTS_1_11
    for name in NEW:
        assert parse_event(name + "@1").version == 1


def test_manifest_with_feeds_permission_loads() -> None:
    m = ModuleManifest.model_validate(manifest())
    assert "content.feeds" in m.permissions
    m.check_core_compat("1.12.0")


def test_unknown_permission_refused() -> None:
    with pytest.raises(ValidationError):
        ModuleManifest.model_validate(manifest(permissions=["content.feedz"]))


def test_feeds_permission_requires_1_12_and_content_type() -> None:
    with pytest.raises(ValidationError):
        ModuleManifest.model_validate(manifest(core=">=1.1 <1.12"))
    with pytest.raises(ValidationError):
        ModuleManifest.model_validate(
            manifest(kind="notifier", targets=["server"], content=None, core=">=1.12 <2.0")
        )


def test_existing_content_type_unchanged() -> None:
    ModuleManifest.model_validate(manifest(core=">=1.1 <2.0", permissions=[]))


def test_contract_surface() -> None:
    assert "content.feeds" in permissions.PERMISSIONS
    assert hasattr(FeedsApi, "ensure") and hasattr(SafeHttp, "get")
    assert SafeHttpError("net.timeout").code == "net.timeout"
    assert PlayerItemSpec(props={}).feeds == []
    req = FeedRequest(params={"b": 1, "a": 2}, refresh_s=600)
    assert compute_feed_id("m", req.canonical()) == compute_feed_id(
        "m", {"params": {"a": 2, "b": 1}}
    )
    assert len(compute_feed_id("m", req.canonical())) == 32


async def test_fetch_feed_default_not_implemented() -> None:
    class C(ContentType):
        pass

    with pytest.raises(NotImplementedError):
        await C().fetch_feed(FeedRequest(params={}, refresh_s=600), None)  # type: ignore[arg-type]
