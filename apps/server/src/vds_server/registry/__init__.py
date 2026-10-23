"""Registre des modules : découverte, synchronisation en base, cycle de vie à chaud."""

from .discovery import Discovered, discover
from .service import ModuleRegistry

__all__ = ["Discovered", "ModuleRegistry", "discover"]
