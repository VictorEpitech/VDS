"""Contrat vds-core 1.10.0 : droits par périmètre (additif sur 1.9)."""

from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_9,
    CORE_EVENT_PAYLOAD_1_10,
    CORE_EVENTS_1_9,
    CORE_EVENTS_1_10,
    parse_event,
)
from vds_sdk.events import CRITICAL_EVENTS_1_9, CRITICAL_EVENTS_1_10

NEW = {"user.scope_changed", "user.role_changed"}


def test_version() -> None:
    assert CORE_API_VERSION in {"1.10.0", "1.11.0", "1.12.0"}


def test_events_1_10_additive() -> None:
    assert NEW == CORE_EVENTS_1_10 - CORE_EVENTS_1_9
    assert CORE_EVENT_PAYLOAD_1_9.items() <= CORE_EVENT_PAYLOAD_1_10.items()
    assert CRITICAL_EVENTS_1_10 == CRITICAL_EVENTS_1_9
    assert CORE_EVENT_PAYLOAD_1_10["user.scope_changed"] == ("user_id", "scopes")
    # owner est optionnel sur media/playlist/layout : pas une clé requise
    for name, keys in CORE_EVENT_PAYLOAD_1_10.items():
        assert "owner" not in keys, name
    for name in NEW:
        assert parse_event(name + "@1").version == 1
