import asyncio
from pathlib import Path

from vds_agent.testing import load_driver_class, load_fakes
from vds_sdk import load_manifest
from vds_sdk.agent import DetectHint, PowerTarget

fakes = load_fakes("power.wol")
Driver = load_driver_class("power.wol")
SID = "11111111-1111-1111-1111-111111111111"


def tgt(**cfg: object) -> PowerTarget:
    return PowerTarget(screen_id=SID, site_timezone="UTC", config=cfg)  # type: ignore[arg-type]


def test_manifest() -> None:
    assert load_manifest(Path(__file__).parents[1] / "module.json").id == "power.wol"


async def test_magic_packet_sent() -> None:
    async with fakes.UdpCapture() as cap:
        r = await Driver().set_power(
            tgt(mac="AA-BB-CC-DD-EE-FF", broadcast="127.0.0.1", port=cap.port), "on"
        )
        await asyncio.sleep(0.5)
    assert r.ok and not r.verified
    assert cap.packets[0] == b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16
    assert len(cap.packets[0]) == 102


async def test_off_unsupported_and_bad_mac() -> None:
    d = Driver()
    assert (await d.set_power(tgt(mac="aa:bb:cc:dd:ee:ff"), "off")).code == "power.wol.unsupported"
    assert (await d.set_power(tgt(mac="zz"), "on")).code == "power.wol.mac_invalid"
    assert await d.get_state(tgt()) == "unknown"


async def test_detect() -> None:
    d = Driver()
    assert await d.detect(DetectHint()) is None
    r = await d.detect(DetectHint(mac="AA:BB:CC:DD:EE:FF"))
    assert r and r.proposed_config == {"mac": "aa:bb:cc:dd:ee:ff"}
