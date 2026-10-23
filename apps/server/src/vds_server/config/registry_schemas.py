"""ConfigSchemaProvider branché sur le registre des modules (T5) : schéma `core` + modules."""

from typing import Any

from vds_server.config.schemas import CORE_NAMESPACE, CORE_SCHEMA
from vds_server.registry import ModuleRegistry


class RegistrySchemaProvider:
    def __init__(self, registry: ModuleRegistry) -> None:
        self._registry = registry

    def get_schema(self, namespace: str) -> dict[str, Any] | None:
        if namespace == CORE_NAMESPACE:
            return CORE_SCHEMA
        return self._registry.config_schema(namespace)
