"""Fourniture des schémas de config par espace de noms (interface branchée par le registre, T5)."""

from collections.abc import Mapping
from typing import Any, Protocol

CORE_NAMESPACE = "core"

CORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "timezone": {
            "type": "string",
            "minLength": 1,
            "title": "config.core.timezone.title",
            "description": "config.core.timezone.description",
            "default": "Europe/Paris",
            "x-vds-level": "simple",
        },
        "locale": {
            "type": "string",
            "enum": ["fr", "en"],
            "title": "config.core.locale.title",
            "description": "config.core.locale.description",
            "default": "fr",
            "x-vds-level": "simple",
        },
        "media_max_upload_mb": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16384,
            "title": "config.core.media_max_upload_mb.title",
            "description": "config.core.media_max_upload_mb.description",
            "default": 2048,
            "x-vds-level": "advanced",
            "x-vds-scopes": ["organization"],
        },
    },
}


class ConfigSchemaProvider(Protocol):
    """Retourne le `config.schema.json` d'un espace de noms, ou None s'il est inconnu."""

    def get_schema(self, namespace: str) -> dict[str, Any] | None: ...


class StaticSchemaProvider:
    """Fournisseur en mémoire : le schéma `core` + des schémas fournis (tests, démarrage)."""

    def __init__(self, schemas: Mapping[str, dict[str, Any]] | None = None) -> None:
        self._schemas: dict[str, dict[str, Any]] = {CORE_NAMESPACE: CORE_SCHEMA}
        self._schemas.update(schemas or {})

    def get_schema(self, namespace: str) -> dict[str, Any] | None:
        return self._schemas.get(namespace)
