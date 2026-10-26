"""Kinds de module côté agent de site (contrat vds-core 1.3.0)."""

from typing import Protocol

from ..module import ModuleContext
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
