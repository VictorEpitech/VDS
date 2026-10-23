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
