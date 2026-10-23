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
