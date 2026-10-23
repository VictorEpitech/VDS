"""État local d'un module sur l'agent de site (clé-valeur JSON, jamais remonté au serveur)."""

from typing import Any, Protocol

JsonValue = Any


class StateStore(Protocol):
    """Stockage clé-valeur JSON propre à un module (SQLite côté agent)."""

    async def get(self, key: str, default: JsonValue = None) -> JsonValue: ...

    async def set(self, key: str, value: JsonValue) -> None: ...

    async def delete(self, key: str) -> None: ...
