"""Niveaux d'affichage : bandes constantes de `schedule.priority` (sans migration)."""

from typing import Literal

PriorityLevel = Literal["background", "normal", "high"]

NORMAL_FLOOR = 0
HIGH_FLOOR = 100
DEFAULT_PRIORITY: dict[PriorityLevel, int] = {"background": -100, "normal": 0, "high": 100}


def priority_level(p: int) -> PriorityLevel:
    """Fond < 0, Normale 0..99, Prioritaire >= 100."""
    if p < NORMAL_FLOOR:
        return "background"
    return "high" if p >= HIGH_FLOOR else "normal"
