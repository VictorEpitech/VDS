"""Noms d'événements : `domaine.verbe_passé`, suffixe optionnel `@N` (version de payload)."""

import re
from typing import NamedTuple

EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+(@[1-9][0-9]*)?$")


class EventRef(NamedTuple):
    name: str
    version: int


def parse_event(text: str) -> EventRef:
    if not EVENT_NAME_RE.match(text):
        raise ValueError(f"invalid event name: {text!r}")
    name, _, ver = text.partition("@")
    return EventRef(name, int(ver) if ver else 1)


CORE_EVENT_DOMAINS_1_1: frozenset[str] = frozenset({"media", "playlist", "schedule", "screen"})

CORE_EVENTS_1_1: frozenset[str] = frozenset(
    {
        "media.uploaded",
        "media.ready",
        "media.failed",
        "media.deleted",
        "playlist.created",
        "playlist.updated",
        "playlist.deleted",
        "schedule.created",
        "schedule.updated",
        "schedule.deleted",
        "screen.paired",
        "screen.unpaired",
    }
)

CORE_EVENT_PAYLOAD_1_1: dict[str, tuple[str, ...]] = {
    "media.uploaded": ("media_id", "kind", "mime"),
    "media.ready": ("media_id",),
    "media.failed": ("media_id", "code"),
    "media.deleted": ("media_id",),
    "playlist.created": ("playlist_id",),
    "playlist.updated": ("playlist_id",),
    "playlist.deleted": ("playlist_id",),
    "schedule.created": ("schedule_id",),
    "schedule.updated": ("schedule_id",),
    "schedule.deleted": ("schedule_id",),
    "screen.paired": ("screen_id", "device_id", "platform"),
    "screen.unpaired": ("screen_id", "device_id", "reason"),
}

CORE_EVENT_DOMAINS_1_2: frozenset[str] = CORE_EVENT_DOMAINS_1_1 | {
    "agent",
    "power",
    "incident",
    "cache",
}

CORE_EVENT_PAYLOAD_1_2: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_1,
    "agent.enrolled": ("agent_id", "site_id"),
    "agent.rotated": ("agent_id", "site_id"),
    "agent.revoked": ("agent_id", "site_id"),
    "agent.connected": ("agent_id", "site_id", "version"),
    "agent.disconnected": ("agent_id", "site_id", "reason"),
    "power.requested": ("request_id", "screen_id", "state", "reason"),
    "power.changed": ("screen_id", "state", "driver", "verified"),  # + request_id optionnel
    "power.failed": ("screen_id", "state", "attempted", "code"),  # + request_id optionnel
    "power.plan_changed": ("scope_level",),  # + scope_id optionnel
    "screen.status_changed": ("screen_id", "status", "previous"),
    "incident.opened": ("incident_id", "kind", "subject_type", "subject_id", "severity"),
    "incident.resolved": ("incident_id", "kind", "subject_type", "subject_id", "severity"),
    "cache.synced": ("site_id", "items", "bytes", "missing"),
}

CORE_EVENTS_1_2: frozenset[str] = CORE_EVENTS_1_1 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_2) - set(CORE_EVENT_PAYLOAD_1_1)
)

# Événements dont la livraison est critique (outbox obligatoire).
CRITICAL_EVENTS_1_2: frozenset[str] = frozenset(
    {"agent.revoked", "power.requested", "power.failed", "incident.opened", "incident.resolved"}
)

# --- vds-core 1.3.0 (additif) : provisioning d'écran -------------------------------
CORE_EVENT_PAYLOAD_1_3: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_2,
    "screen.provision_started": ("provision_id", "screen_id", "site_id", "platform", "actor_id"),
    "screen.provision_progressed": ("provision_id", "screen_id", "step", "status", "code"),
    "screen.provisioned": ("provision_id", "screen_id", "kiosk", "version_code"),
    "screen.provision_failed": ("provision_id", "screen_id", "step", "code"),
}

CORE_EVENTS_1_3: frozenset[str] = CORE_EVENTS_1_2 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_3) - set(CORE_EVENT_PAYLOAD_1_2)
)

# Fiable agent -> serveur (le relais agent->bus reste décidé côté serveur, lot P2/P4).
CRITICAL_EVENTS_1_3: frozenset[str] = CRITICAL_EVENTS_1_2 | {"screen.provision_progressed"}
