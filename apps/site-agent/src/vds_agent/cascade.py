"""Cascade d'alimentation : essai du pilote, relecture de l'état réel, retry, pilote suivant."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from vds_sdk.agent import PowerDriver, PowerState, PowerTarget

log = logging.getLogger("vds.agent.cascade")

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class ScreenDrivers:
    """Pilotes agent actifs ET configurés pour un écran, dans l'ordre de la cascade."""

    screen_id: UUID
    site_timezone: str
    drivers: list[tuple[str, PowerDriver, PowerTarget]] = field(default_factory=list)


@dataclass
class CascadeOutcome:
    ok: bool
    state: Literal["on", "off"]
    driver: str | None
    verified: bool
    attempted: list[str]
    code: str | None


def matches(wanted: Literal["on", "off"], observed: PowerState) -> bool:
    return observed == "on" if wanted == "on" else observed in ("off", "standby")


class Cascade:
    def __init__(
        self,
        *,
        retries: int = 2,
        verify_delay_s: float = 8.0,
        call_timeout_s: float = 30.0,
        read_timeout_s: float = 10.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.retries = max(1, retries)  # nombre d'essais par pilote (2 = un retry)
        self.verify_delay_s = verify_delay_s
        self.call_timeout_s = call_timeout_s
        self.read_timeout_s = read_timeout_s
        self._sleep = sleep

    async def read_state(self, driver: PowerDriver, target: PowerTarget) -> PowerState:
        try:
            return await asyncio.wait_for(driver.get_state(target), self.read_timeout_s)
        except Exception as e:  # noqa: BLE001 - un pilote ne doit jamais casser la cascade
            log.warning("power.read_failed", extra={"err": type(e).__name__})
            return "unknown"

    async def _verify(
        self,
        wanted: Literal["on", "off"],
        own: tuple[str, PowerDriver, PowerTarget],
        screen: ScreenDrivers,
    ) -> tuple[PowerState, bool]:
        """Relit l'état par le pilote lui-même, sinon par un autre pilote capable de `state`."""
        readers = [own, *(d for d in screen.drivers if d[0] != own[0])]
        for _, drv, tgt in readers:
            if "power.state" not in drv.capabilities:
                continue
            st = await self.read_state(drv, tgt)
            if st != "unknown":
                return st, True
        return "unknown", False

    async def run(self, screen: ScreenDrivers, wanted: Literal["on", "off"]) -> CascadeOutcome:
        attempted: list[str] = []
        last_code: str | None = None
        unverified: str | None = None
        cap = f"power.{wanted}"
        for entry in screen.drivers:
            drv_id, drv, target = entry
            if cap not in drv.capabilities:
                continue
            attempted.append(drv_id)
            for attempt in range(self.retries):
                try:
                    res = await asyncio.wait_for(drv.set_power(target, wanted), self.call_timeout_s)
                except TimeoutError:
                    last_code = f"{drv_id}.timeout"
                    continue
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "power.driver_error", extra={"driver": drv_id, "err": type(e).__name__}
                    )
                    last_code = f"{drv_id}.error"
                    continue
                if not res.ok:
                    last_code = res.code or f"{drv_id}.failed"
                    if res.code and res.code.endswith(("unsupported", "not_paired")):
                        break  # inutile de réessayer
                    continue
                await self._sleep(self.verify_delay_s)
                observed, readable = await self._verify(wanted, entry, screen)
                if readable and matches(wanted, observed):
                    return CascadeOutcome(True, wanted, drv_id, True, attempted, None)
                if not readable:
                    unverified = unverified or drv_id
                    break  # aucune relecture possible : un retry n'apprendrait rien
                last_code = "power.state_mismatch"
                log.info("power.mismatch", extra={"driver": drv_id, "attempt": attempt})
            # pilote suivant
        if unverified is not None:
            return CascadeOutcome(True, wanted, unverified, False, attempted, "power.unverified")
        return CascadeOutcome(False, wanted, None, False, attempted, last_code or "power.no_driver")


def event_for(
    outcome: CascadeOutcome, screen_id: UUID, request_id: str | None
) -> tuple[str, dict[str, Any]]:
    base: dict[str, Any] = {"screen_id": str(screen_id), "state": outcome.state}
    if request_id:
        base["request_id"] = request_id
    if outcome.ok:
        return "power.changed", {**base, "driver": outcome.driver, "verified": outcome.verified}
    return "power.failed", {**base, "attempted": outcome.attempted, "code": outcome.code}


def now_utc() -> datetime:
    return datetime.now(UTC)
