"""Contrat vds-core 1.13.0 : validation de publication (additif sur 1.12)."""

from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_12,
    CORE_EVENT_PAYLOAD_1_13,
    CORE_EVENTS_1_12,
    CORE_EVENTS_1_13,
    parse_event,
)
from vds_sdk.events import CRITICAL_EVENTS_1_12, CRITICAL_EVENTS_1_13

NEW = {
    "publication.submitted",
    "publication.withdrawn",
    "publication.approved",
    "publication.rejected",
}


def test_version_and_events() -> None:
    assert CORE_API_VERSION in {"1.13.0", "1.14.0"}
    assert NEW == CORE_EVENTS_1_13 - CORE_EVENTS_1_12
    assert CORE_EVENT_PAYLOAD_1_12.items() <= CORE_EVENT_PAYLOAD_1_13.items()
    assert CRITICAL_EVENTS_1_13 == CRITICAL_EVENTS_1_12
    for name in NEW:
        assert parse_event(name + "@1").version == 1
        assert CORE_EVENT_PAYLOAD_1_13[name][0] == "v"
