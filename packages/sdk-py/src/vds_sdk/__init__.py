"""Contrat de module VDS (vds-core 1.0.0)."""

CORE_API_VERSION = "1.0.0"

from .bus import Event, EventBus, ScopedBus  # noqa: E402
from .config_schema import validate_config_schema  # noqa: E402
from .errors import (  # noqa: E402
    AccessDenied,
    ConfigSchemaError,
    ContractError,
    IncompatibleCore,
    PermissionDenied,
    UndeclaredEvent,
)
from .manifest import ModuleManifest, load_manifest  # noqa: E402
from .module import Health, Module, ModuleContext, ScopeRef  # noqa: E402

__all__ = [
    "CORE_API_VERSION",
    "AccessDenied",
    "ConfigSchemaError",
    "ContractError",
    "Event",
    "EventBus",
    "Health",
    "IncompatibleCore",
    "Module",
    "ModuleContext",
    "ModuleManifest",
    "PermissionDenied",
    "ScopeRef",
    "ScopedBus",
    "UndeclaredEvent",
    "load_manifest",
    "validate_config_schema",
]
