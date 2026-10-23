"""Statut d'un écran : fonction pure (docs/architecture-lot3.md § 5)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

ScreenStatus = Literal["online", "offline", "scheduled_off", "unseen", "revoked"]


@dataclass(frozen=True)
class Thresholds:
    grace_s: int = 600
    screen_offline_after_s: int = 180
    agent_offline_after_s: int = 300

    @classmethod
    def from_config(cls, cfg: Mapping[str, object]) -> "Thresholds":
        d = cls()

        def pick(key: str, default: int) -> int:
            v = cfg.get(f"supervision.{key}")
            return v if isinstance(v, int) and not isinstance(v, bool) else default

        return cls(
            pick("grace_s", d.grace_s),
            pick("screen_offline_after_s", d.screen_offline_after_s),
            pick("agent_offline_after_s", d.agent_offline_after_s),
        )


def screen_status(
    *,
    has_active_device: bool,
    has_revoked_device: bool,
    last_seen_at: datetime | None,
    desired: str | None,
    desired_since: datetime | None,
    now: datetime,
    thresholds: Thresholds,
) -> ScreenStatus:
    """`scheduled_off` : aucune alerte. `offline` : heartbeats manqués alors que l'écran doit
    être allumé depuis plus de `grace_s` (couvre le démarrage de la TV)."""
    if not has_active_device:
        return "revoked" if has_revoked_device else "unseen"
    if desired == "off":
        return "scheduled_off"
    if last_seen_at is None:
        return "unseen"
    if now - last_seen_at < timedelta(seconds=thresholds.screen_offline_after_s):
        return "online"
    if desired_since is not None and now - desired_since < timedelta(seconds=thresholds.grace_s):
        return "online"  # allumage récent : la TV démarre, pas encore de statut hors ligne
    return "offline"
