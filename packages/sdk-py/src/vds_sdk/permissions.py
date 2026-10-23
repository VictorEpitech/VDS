"""Catalogue fermé de permissions (contrat 1.0)."""

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
]
PERMISSIONS: frozenset[str] = frozenset(get_args(Permission))
