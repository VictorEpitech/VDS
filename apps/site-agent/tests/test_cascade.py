from typing import Any, ClassVar, Literal
from uuid import uuid4

from vds_agent.cascade import Cascade, ScreenDrivers, event_for
from vds_sdk.agent import PowerDriver, PowerResult, PowerState, PowerTarget


class D(PowerDriver):
    capabilities: ClassVar[frozenset[Any]] = frozenset({"power.on", "power.off", "power.state"})

    def __init__(
        self,
        *,
        ok: bool = True,
        effect: bool = True,
        caps: frozenset[Any] | None = None,
        code: str | None = None,
    ) -> None:
        self.ok, self.effect, self.code = ok, effect, code
        self.s: PowerState = "off"
        self.calls = 0
        if caps is not None:
            self.capabilities = caps

    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
        self.calls += 1
        if self.ok and self.effect:
            self.s = state
        return PowerResult(ok=self.ok, state="unknown", verified=False, code=self.code)

    async def get_state(self, target: PowerTarget) -> PowerState:
        return self.s if "power.state" in self.capabilities else "unknown"


async def nosleep(_: float) -> None:
    return None


def screen(**drivers: D) -> ScreenDrivers:
    sid = uuid4()
    t = PowerTarget(screen_id=sid, site_timezone="UTC")
    return ScreenDrivers(sid, "UTC", [(k, v, t) for k, v in drivers.items()])


def cas(retries: int = 2) -> Cascade:
    return Cascade(retries=retries, verify_delay_s=0, sleep=nosleep)


async def test_first_driver_verified() -> None:
    a, b = D(), D()
    out = await cas().run(screen(a=a, b=b), "on")
    assert (out.ok, out.driver, out.verified, out.attempted) == (True, "a", True, ["a"])
    assert b.calls == 0


async def test_retry_then_next_driver() -> None:
    a, b = D(effect=False), D()
    out = await cas(2).run(screen(a=a, b=b), "on")
    assert a.calls == 2 and out.driver == "b" and out.verified and out.attempted == ["a", "b"]


async def test_driver_refusal_skips_to_next_and_off_standby_ok() -> None:
    a = D(ok=False, code="power.adb.unreachable")
    b = D()
    b.s = "standby"
    out = await cas().run(screen(a=a, b=b), "off")
    assert out.ok and out.driver == "b"


async def test_all_fail_emits_failed_event() -> None:
    sc = screen(a=D(ok=False, code="x.fail"), b=D(effect=False))
    out = await cas().run(sc, "on")
    assert not out.ok and out.code == "power.state_mismatch"
    name, payload = event_for(out, sc.screen_id, "req-1")
    assert (
        name == "power.failed"
        and payload["attempted"] == ["a", "b"]
        and payload["request_id"] == "req-1"
    )


async def test_wol_verified_by_other_driver_reader() -> None:
    wol = D(caps=frozenset({"power.on"}))
    reader = D()
    reader.s = "on"  # la TV est bien allumée quand on relit
    out = await cas().run(screen(wol=wol, reader=reader), "on")
    assert out.driver == "wol" and out.verified


async def test_unverifiable_is_unverified_changed() -> None:
    wol = D(caps=frozenset({"power.on"}))
    sc = screen(wol=wol)
    out = await cas().run(sc, "on")
    assert out.ok and not out.verified and wol.calls == 1
    assert event_for(out, sc.screen_id, None)[0] == "power.changed"
    off = await cas().run(sc, "off")  # aucun pilote capable
    assert not off.ok and off.code == "power.no_driver"


async def test_timeout_bounded() -> None:
    import asyncio

    class Slow(D):
        async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
            await asyncio.sleep(5)
            return PowerResult(ok=True, state="on", verified=True)

    c = Cascade(retries=1, verify_delay_s=0, call_timeout_s=0.05, sleep=nosleep)
    out = await c.run(screen(slow=Slow()), "on")
    assert not out.ok and out.code == "slow.timeout"
