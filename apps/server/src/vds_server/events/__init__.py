"""Bus d'événements du noyau (outbox pour les événements critiques)."""

from .bus import CRITICAL_EVENTS, CoreBus

__all__ = ["CRITICAL_EVENTS", "CoreBus"]
