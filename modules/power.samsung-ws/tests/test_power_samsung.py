import asyncio
from pathlib import Path

from vds_agent.testing import MemoryState, load_driver_class, load_fakes, make_ctx
from vds_sdk import load_manifest
from vds_sdk.agent import DetectHint, PowerTarget

fakes = load_fakes("power.samsung-ws")
Driver = load_driver_class("power.samsung-ws")
mod = __import__("sys").modules[Driver.__module__.rsplit(".", 1)[0] + ".samsung"]
SID = "11111111-1111-1111-1111-111111111111"


class FakeTransportRest(mod.WsSamsungTransport):  # type: ignore[misc]
    """Transport réel, mais REST redirigé vers le port du faux serveur."""

    def __init__(self, tv: object) -> None:
        self.tv = tv

    async def device_info(self, host, timeout):  # type: ignore[no-untyped-def]
        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(f"http://{host}:{self.tv.rest_port}/api/v2/")  # type: ignore[attr-defined]
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError:
            return None


class Udp:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, packet: bytes, broadcast: str, port: int) -> None:
        self.sent.append(packet)


def target(tv: object, **cfg: object) -> PowerTarget:
    return PowerTarget(
        screen_id=SID,
        site_timezone="UTC",
        address="127.0.0.1",  # type: ignore[arg-type]
        mac="aa:bb:cc:00:11:22",
        config={"port": tv.ws_port, "name": "VDS", **cfg},  # type: ignore[attr-defined]
    )


async def driver(tv, state=None, udp=None):  # type: ignore[no-untyped-def]
    d = Driver(FakeTransportRest(tv), udp or Udp(), ws_port=tv.ws_port)
    await d.setup(make_ctx("power.samsung-ws", state))
    return d


def test_manifest() -> None:
    assert load_manifest(Path(__file__).parents[1] / "module.json").kind == "power-driver"


async def test_pairing_pin_and_off_toggle_guard() -> None:
    st = MemoryState()
    async with fakes.FakeSamsungTv("accept", power="on") as tv:
        d = await driver(tv, st)
        t = target(tv)
        assert await d.get_state(t) == "on"
        assert (await d.set_power(t, "off")).ok
        assert tv.keys == ["KEY_POWER"] and tv.power == "standby"
        assert tv.names == ["VDS"]
        assert st.data["token:127.0.0.1"] == tv.token and len(st.data["pin:127.0.0.1"]) == 64
        assert await d.get_state(t) == "standby"
        # déjà en veille : aucune touche (KEY_POWER rallumerait)
        r = await d.set_power(t, "off")
        assert r.ok and r.verified and tv.keys == ["KEY_POWER"]
        # le jeton mémorisé est réutilisé
        assert (await d.set_power(t, "on")).ok
        assert tv.tokens_seen[-1] == tv.token and tv.power == "on"


async def test_power_on_from_unreachable_uses_wol() -> None:
    udp = Udp()
    async with fakes.FakeSamsungTv() as tv:
        tv.rest_enabled = False
        d = await driver(tv, udp=udp)
        r = await d.set_power(target(tv), "on")
        assert r.ok and not r.verified and tv.keys == []
        assert udp.sent and len(udp.sent[0]) == 102
        off = await d.set_power(target(tv), "off")
        assert not off.ok and off.code == "power.samsung.unreachable"
        assert await d.get_state(target(tv)) == "unknown"
        assert await d.get_state(target(tv, unreachable_means_off=True)) == "off"


async def test_denied_and_cert_change() -> None:
    st = MemoryState()
    async with fakes.FakeSamsungTv("deny") as tv:
        d = await driver(tv, st)
        r = await d.set_power(target(tv), "off")
        assert r.code == "power.samsung.not_paired"
    st.data["pin:127.0.0.1"] = "0" * 64
    async with fakes.FakeSamsungTv("accept") as tv:
        d = await driver(tv, st)
        assert (await d.set_power(target(tv), "off")).code == "power.samsung.cert_changed"
        assert tv.tokens_seen == []  # jeton jamais envoyé à une TV non épinglée


async def test_detect_pairs_in_background() -> None:
    st = MemoryState()
    async with fakes.FakeSamsungTv("accept") as tv:
        d = await driver(tv, st)
        res = await d.detect(DetectHint(address="127.0.0.1"))
        assert res and res.proposed_config["mac"] == "aa:bb:cc:00:11:22"
        assert res.requires_user_action is None  # accepté pendant la détection
        assert st.data["token:127.0.0.1"] == tv.token
    async with fakes.FakeSamsungTv("ignore") as tv2:
        d2 = await driver(tv2, MemoryState())
        res2 = await asyncio.wait_for(d2.detect(DetectHint(address="127.0.0.1")), 12)
        assert res2 and res2.requires_user_action == "power.samsung.accept_on_tv"
        await d2.teardown()
        await d.teardown()
    assert await Driver(FakeTransportRest(tv), Udp()).detect(DetectHint()) is None
