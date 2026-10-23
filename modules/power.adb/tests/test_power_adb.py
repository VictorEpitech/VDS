import json
from pathlib import Path

from vds_agent.testing import MemoryState, load_driver_class, load_fakes, make_ctx
from vds_sdk import load_manifest
from vds_sdk.agent import DetectHint, PowerTarget

fakes = load_fakes("power.adb")
Driver = load_driver_class("power.adb")
SID = "11111111-1111-1111-1111-111111111111"


def target(port: int, **cfg: object) -> PowerTarget:
    return PowerTarget(
        screen_id=SID,
        site_timezone="Europe/Paris",
        address="127.0.0.1",  # type: ignore[arg-type]
        config={"port": port, "timeout_ms": 1500, **cfg},
    )


async def make_driver(state: MemoryState | None = None):  # type: ignore[no-untyped-def]
    d = Driver()
    await d.setup(make_ctx("power.adb", state))
    return d


def test_manifest_valid() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert m.kind == "power-driver"


async def test_pairing_then_sleep_and_state() -> None:
    state = MemoryState()
    async with fakes.FakeAdbTv("auth") as tv:
        d = await make_driver(state)
        t = target(tv.port)
        r = await d.set_power(t, "off")
        assert r.ok and tv.commands == ["shell:input keyevent KEYCODE_SLEEP"]
        assert await d.get_state(t) == "standby"
        assert (await d.set_power(t, "on")).ok
        assert await d.get_state(t) == "on"
        assert len(tv.accepted) == 1  # clé enregistrée une seule fois
    assert "BEGIN PRIVATE KEY" in state.data["adb_private_key"]


async def test_big_output_chunks() -> None:
    async with fakes.FakeAdbTv("open") as tv:
        d = await make_driver()
        assert await d.get_state(target(tv.port)) == "on"


async def test_denied_is_unauthorized() -> None:
    async with fakes.FakeAdbTv("deny") as tv:
        d = await make_driver()
        r = await d.set_power(target(tv.port), "on")
        assert not r.ok and r.code == "power.adb.unauthorized"


async def test_timeout_and_unreachable() -> None:
    async with fakes.FakeAdbTv("hang") as tv:
        d = await make_driver()
        r = await d.set_power(target(tv.port, timeout_ms=500), "on")
        assert r.code == "power.adb.timeout"
        assert await d.get_state(target(tv.port, timeout_ms=500)) == "unknown"
        assert (
            await d.get_state(target(tv.port, timeout_ms=500, unreachable_means_off=True)) == "off"
        )
    d = await make_driver()
    assert (await d.set_power(target(1), "on")).code == "power.adb.unreachable"


async def test_stls_not_supported() -> None:
    async with fakes.FakeAdbTv("stls") as tv:
        d = await make_driver()
        assert (await d.set_power(target(tv.port), "on")).code == "power.adb.tls_required"


async def test_detect_without_address() -> None:
    d = await make_driver()
    assert await d.detect(DetectHint()) is None


def test_schema_json_loads() -> None:
    json.loads((Path(__file__).parents[1] / "config.schema.json").read_text())
