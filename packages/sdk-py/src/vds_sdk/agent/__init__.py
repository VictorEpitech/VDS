"""Kinds de module côté agent de site (contrat vds-core 1.5.0)."""

from typing import Protocol

from ..module import ModuleContext
from .discovery import (
    DISCOVERY_PORTS,
    DiscoveredDevice,
    Discoverer,
    DiscoveryNet,
    DiscoveryScope,
    DiscoveryScopeError,
    MdnsRecord,
    SsdpDescription,
    SsdpReply,
    check_discoverer,
)
from .enroll import (
    ENROLL_CODE_PREFIX,
    AgentEnrolled,
    AgentEnrollmentCreated,
    AgentEnrollmentRevoked,
    EnrollRequest,
    EnrollResponse,
    LocalHandoff,
    LocalPresence,
    parse_presence,
)
from .power import (
    Capability,
    DetectHint,
    DetectResult,
    PowerDriver,
    PowerResult,
    PowerState,
    PowerTarget,
)
from .provision import (
    ApkRef,
    ApkSource,
    ProvisionOutcome,
    ProvisionRequest,
    ScreenProvisioner,
    StepReport,
    check_provisioner,
)
from .state import StateStore


class AgentModuleContext(ModuleContext, Protocol):
    """Contexte d'un module exécuté sur l'agent : ajoute `state`."""

    state: StateStore


__all__ = [
    "DISCOVERY_PORTS",
    "DiscoveredDevice",
    "Discoverer",
    "DiscoveryNet",
    "DiscoveryScope",
    "DiscoveryScopeError",
    "MdnsRecord",
    "SsdpDescription",
    "SsdpReply",
    "check_discoverer",
    "ENROLL_CODE_PREFIX",
    "AgentEnrolled",
    "AgentEnrollmentCreated",
    "AgentEnrollmentRevoked",
    "EnrollRequest",
    "EnrollResponse",
    "LocalHandoff",
    "LocalPresence",
    "parse_presence",
    "AgentModuleContext",
    "ApkRef",
    "ApkSource",
    "Capability",
    "DetectHint",
    "DetectResult",
    "PowerDriver",
    "PowerResult",
    "PowerState",
    "PowerTarget",
    "ProvisionOutcome",
    "ProvisionRequest",
    "ScreenProvisioner",
    "StepReport",
    "StateStore",
    "check_provisioner",
]
