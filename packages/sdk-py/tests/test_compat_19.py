"""Contrat vds-core 1.9.0 : mises en page et forçage de zone (additif sur 1.8)."""

from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_8,
    CORE_EVENT_PAYLOAD_1_9,
    CORE_EVENTS_1_8,
    CORE_EVENTS_1_9,
    parse_event,
)
from vds_sdk.events import CRITICAL_EVENTS_1_8, CRITICAL_EVENTS_1_9
from vds_sdk.permissions import PERMISSIONS

NEW = {"layout.created", "layout.updated", "layout.deleted"}


def test_version() -> None:
    assert CORE_API_VERSION == "1.9.0"


def test_events_1_9_additive() -> None:
    assert NEW == CORE_EVENTS_1_9 - CORE_EVENTS_1_8
    assert CORE_EVENT_PAYLOAD_1_8.items() <= CORE_EVENT_PAYLOAD_1_9.items()
    assert CRITICAL_EVENTS_1_9 == CRITICAL_EVENTS_1_8
    assert CORE_EVENT_PAYLOAD_1_9["layout.updated"] == ("layout_id", "revision")
    # zone_ids est optionnel sur override.* : pas une clé requise
    for name in ("override.created", "override.updated", "override.stopped", "override.expired"):
        assert "zone_ids" not in CORE_EVENT_PAYLOAD_1_9[name]
    for name in NEW:
        assert parse_event(name + "@1").version == 1


def test_permissions_1_9() -> None:
    assert {"layouts.read", "layouts.write"} <= PERMISSIONS
