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

# --- vds-core 1.4.0 (additif) : enrôlement d'agent ---------------------------------
# `agent.enrolled` (déjà en 1.2 : agent_id, site_id) reste valide avec ces seules clés ; le
# payload enrichi (vds_sdk.agent.enroll.AgentEnrolled) est celui de l'enrôlement par code.
CORE_EVENT_PAYLOAD_1_4: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_3,
    "agent.enrollment_created": ("enrollment_id", "site_id", "channel", "expires_at", "created_by"),
    "agent.enrollment_revoked": ("enrollment_id", "site_id", "reason"),
}

CORE_EVENTS_1_4: frozenset[str] = CORE_EVENTS_1_3 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_4) - set(CORE_EVENT_PAYLOAD_1_3)
)

CRITICAL_EVENTS_1_4: frozenset[str] = CRITICAL_EVENTS_1_3 | {"agent.enrolled"}

# --- vds-core 1.5.0 (additif) : découverte LAN -------------------------------------
# Bus simple, aucun événement critique. Aucune adresse, MAC ni nom d'appareil dans un payload.
CORE_EVENT_PAYLOAD_1_5: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_4,
    "discovery.started": ("scan_id", "site_id", "agent_id", "requested_by", "probe_ports"),
    "discovery.completed": (
        "scan_id",
        "site_id",
        "device_count",
        "capped",
        "truncated",
        "duration_ms",
    ),
    "discovery.failed": ("scan_id", "site_id", "code"),
}

CORE_EVENTS_1_5: frozenset[str] = CORE_EVENTS_1_4 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_5) - set(CORE_EVENT_PAYLOAD_1_4)
)

CRITICAL_EVENTS_1_5: frozenset[str] = CRITICAL_EVENTS_1_4

# --- vds-core 1.6.0 (additif) : mise à jour de l'APK player ------------------------
# Émis par le noyau ; aucun abonné en V1 (point d'accroche notifier). Aucun événement critique.
CORE_EVENT_PAYLOAD_1_6: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_5,
    "screen.apk_updated": ("request_id", "screen_id", "previous_version_code", "version_code"),
    "screen.apk_update_failed": ("request_id", "screen_id", "version_code", "code"),
}

CORE_EVENTS_1_6: frozenset[str] = CORE_EVENTS_1_5 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_6) - set(CORE_EVENT_PAYLOAD_1_5)
)

CRITICAL_EVENTS_1_6: frozenset[str] = CRITICAL_EVENTS_1_5

# --- vds-core 1.7.0 (additif) : forçage avec fin automatique -------------------------
# Émis par le noyau (primitive de planification). Payloads `version: 1` ; les clés ci-dessous
# sont le minimum garanti (le noyau peut en ajouter). `override.updated` : prolongation/note,
# nécessaire pour que l'agent de site recalcule son plan. Aucun événement critique.
CORE_EVENT_PAYLOAD_1_7: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_6,
    "override.created": (
        "override_id",
        "target_type",
        "target_id",
        "kind",
        "playlist_id",
        "starts_at",
        "ends_at",
        "created_by",
        "created_by_module",
    ),
    "override.updated": ("override_id", "target_type", "target_id", "ends_at"),
    "override.stopped": ("override_id", "target_type", "target_id", "ended_at", "ended_by"),
    "override.expired": ("override_id", "target_type", "target_id", "ended_at"),
}

CORE_EVENTS_1_7: frozenset[str] = CORE_EVENTS_1_6 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_7) - set(CORE_EVENT_PAYLOAD_1_6)
)

CRITICAL_EVENTS_1_7: frozenset[str] = CRITICAL_EVENTS_1_6

# --- vds-core 1.8.0 (additif) : médiathèque (expiration, purge, remplacement) ---------
# Payloads `version: 1` ; clés = minimum garanti. `media.purged` et `playlist.purged` sont
# critiques (outbox). `schedule.deleted` gagne la clé optionnelle `reason` ("user" |
# "playlist_purged") sans changer les clés requises (`schedule_id`).
CORE_EVENT_PAYLOAD_1_8: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_7,
    "media.updated": ("media_id", "fields"),
    "media.replaced": ("media_id", "revision", "sha256", "previous_sha256"),
    "media.expired": ("media_id", "expires_at"),
    "media.restored": ("media_id",),
    "media.purged": ("media_id", "playlist_ids"),
    "playlist.expired": ("playlist_id", "expires_at"),
    "playlist.restored": ("playlist_id",),
    "playlist.purged": ("playlist_id", "schedule_ids"),
}

CORE_EVENTS_1_8: frozenset[str] = CORE_EVENTS_1_7 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_8) - set(CORE_EVENT_PAYLOAD_1_7)
)

CRITICAL_EVENTS_1_8: frozenset[str] = CRITICAL_EVENTS_1_7 | {"media.purged", "playlist.purged"}

SCHEDULE_DELETED_REASONS_1_8: frozenset[str] = frozenset({"user", "playlist_purged"})

# --- vds-core 1.9.0 (additif) : mises en page multi-zones et forçage de zone ----------
# Payloads `version: 1`. Les payloads `override.*` existants gagnent la clé optionnelle
# `zone_ids` (list[str] | None ; None = forçage d'écran) sans changer les clés requises.
# Aucun événement critique ajouté.
CORE_EVENT_DOMAINS_1_9: frozenset[str] = CORE_EVENT_DOMAINS_1_2 | {"override", "layout"}

CORE_EVENT_PAYLOAD_1_9: dict[str, tuple[str, ...]] = {
    **CORE_EVENT_PAYLOAD_1_8,
    "layout.created": ("layout_id",),
    "layout.updated": ("layout_id", "revision"),
    "layout.deleted": ("layout_id",),
}

CORE_EVENTS_1_9: frozenset[str] = CORE_EVENTS_1_8 | frozenset(
    set(CORE_EVENT_PAYLOAD_1_9) - set(CORE_EVENT_PAYLOAD_1_8)
)

CRITICAL_EVENTS_1_9: frozenset[str] = CRITICAL_EVENTS_1_8
