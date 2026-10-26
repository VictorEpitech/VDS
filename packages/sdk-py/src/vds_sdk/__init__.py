"""Contrat de module VDS (vds-core 1.3.0)."""

CORE_API_VERSION = "1.3.0"

from . import permissions  # noqa: E402
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
from .events import (  # noqa: E402
    CORE_EVENT_PAYLOAD_1_1,
    CORE_EVENT_PAYLOAD_1_2,
    CORE_EVENT_PAYLOAD_1_3,
    CORE_EVENTS_1_1,
    CORE_EVENTS_1_2,
    CORE_EVENTS_1_3,
    EVENT_NAME_RE,
    parse_event,
)
from .manifest import ModuleManifest, load_manifest  # noqa: E402
from .module import Health, Module, ModuleContext, ScopeRef  # noqa: E402

__all__ = [
    "CORE_API_VERSION",
    "CORE_EVENTS_1_1",
    "CORE_EVENTS_1_2",
    "CORE_EVENTS_1_3",
    "CORE_EVENT_PAYLOAD_1_1",
    "CORE_EVENT_PAYLOAD_1_2",
    "CORE_EVENT_PAYLOAD_1_3",
    "EVENT_NAME_RE",
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
    "parse_event",
    "permissions",
    "validate_config_schema",
]
