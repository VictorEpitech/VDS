"""Catalogue fermé de permissions (contrat 1.0, étendu en 1.2, 1.3 et 1.5)."""

from typing import Literal, get_args

Permission = Literal[
    "screens.read",
    "screens.write",
    "sites.read",
    "config.read.core",
    "users.read",
    "media.read",
    "media.write",
    "lan.access",
    "net.outbound",
    "fs.media",
    "http.routes",
    "db.own-tables",
    "users.provision",
    "power.control",
    "power.calendars.write",
    "incidents.read",
    "screens.provision",
    "network.discover",
]
PERMISSIONS: frozenset[str] = frozenset(get_args(Permission))
