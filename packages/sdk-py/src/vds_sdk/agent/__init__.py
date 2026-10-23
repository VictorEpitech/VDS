"""Kinds de module côté agent de site (contrat vds-core 1.2.0)."""

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
from .state import StateStore


class AgentModuleContext(ModuleContext, Protocol):
    """Contexte d'un module exécuté sur l'agent : ajoute `state`."""

    state: StateStore


__all__ = [
    "AgentModuleContext",
    "Capability",
    "DetectHint",
    "DetectResult",
    "PowerDriver",
    "PowerResult",
    "PowerState",
    "PowerTarget",
    "StateStore",
]
