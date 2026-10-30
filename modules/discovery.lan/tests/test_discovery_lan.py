"""Captures rejouées (synthétiques, écrites d'après les annonces publiques des constructeurs).

Aucun socket : la façade `DiscoveryNet` est un faux qui rejoue mDNS / SSDP / ports / ARP.
"""

import ast
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

import pytest
from vds_agent.testing import load_driver_class, make_ctx
from vds_sdk import load_manifest
from vds_sdk.agent import (
    DiscoveredDevice,
    Discoverer,
    DiscoveryScope,
    MdnsRecord,
    SsdpDescription,
    SsdpReply,
    check_discoverer,
)

MOD = Path(__file__).parents[1]
Driver = load_driver_class("discovery.lan")
A = IPv4Address

DIAL = "urn:dial-multiscreen-org:service:dial:1"
SAMSUNG = "urn:samsung.com:device:RemoteControlReceiver:1"
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"


class ReplayNet:
    def __init__(
        self,
        arp: dict[str, str],
        mdns: list[MdnsRecord] = (),  # type: ignore[assignment]
        ssdp: list[tuple[SsdpReply, SsdpDescription | None]] = (),  # type: ignore[assignment]
        ports: dict[str, set[int]] | None = None,
    ) -> None:
        self.arp = {A(k): v for k, v in arp.items()}
        self._mdns, self._ssdp, self._ports = mdns, ssdp, ports or {}
        self.sweeps = 0

    def neighbors(self) -> Mapping[IPv4Address, str]:
        return self.arp

    async def mdns_browse(
        self, services: Sequence[str], window_s: float
    ) -> AsyncIterator[MdnsRecord]:
        for r in self._mdns:
            yield r

    async def ssdp_search(
        self, targets: Sequence[str], window_s: float
    ) -> AsyncIterator[SsdpReply]:
        for r, _ in self._ssdp:
            yield r

    async def ssdp_description(self, reply: SsdpReply) -> SsdpDescription | None:
        return next((d for r, d in self._ssdp if r == reply), None)

    async def tcp_open(self, ip: IPv4Address, ports: Sequence[int]) -> frozenset[int]:
        raise AssertionError("le module n'utilise que sweep")

    async def sweep(
        self, ports: Sequence[int]
    ) -> AsyncIterator[tuple[IPv4Address, frozenset[int]]]:
        self.sweeps += 1
        for ip, p in self._ports.items():
            yield A(ip), frozenset(p)


def scope(probe_ports: bool = True) -> DiscoveryScope:
    return DiscoveryScope(
        networks=(IPv4Network("192.168.1.0/24"),),
        truncated=False,
        probe_ports=probe_ports,
        deadline=datetime.now(UTC),
    )


async def run(net: ReplayNet, probe_ports: bool = True) -> dict[str, DiscoveredDevice]:
    got: list[DiscoveredDevice] = []

    async def report(d: DiscoveredDevice) -> None:
        got.append(d)

    await Driver().discover(scope(probe_ports), net, report)  # type: ignore[arg-type]
    return {str(d.ip): d for d in got}


def mdns(ip: str, service: str, inst: str, port: int, **txt: str) -> MdnsRecord:
    return MdnsRecord(
        service=service,
        instance=f"{inst}.{service}",
        host=f"{inst}.local",
        ip=A(ip),
        port=port,
        txt=txt,
    )


def ssdp(ip: str, st: str, server: str | None = None, port: int = 9197) -> SsdpReply:
    return SsdpReply(ip=A(ip), st=st, server=server, location=f"http://{ip}:{port}/desc.xml")


# --- fixtures ----------------------------------------------------------------------------
TCL_IP, GTV_IP, SAM_IP, LG_IP, CC_IP, PRN_IP, PHONE_IP = (f"192.168.1.{i}" for i in range(20, 27))
ARP = {
    TCL_IP: "aa:00:00:00:00:01", GTV_IP: "aa:00:00:00:00:02", SAM_IP: "aa:00:00:00:00:03",
    LG_IP: "aa:00:00:00:00:04", CC_IP: "aa:00:00:00:00:05", PRN_IP: "aa:00:00:00:00:06",
    PHONE_IP: "aa:00:00:00:00:07",
}  # fmt: skip


def full_net() -> ReplayNet:
    return ReplayNet(
        ARP,
        mdns=[
            mdns(
                TCL_IP,
                "_googlecast._tcp.local",
                "Google-TV-aaa",
                8009,
                md="Google TV",
                fn="TCL Salon",
            ),
            mdns(TCL_IP, "_androidtvremote2._tcp.local", "TCL-Salon", 6466),
            mdns(
                GTV_IP,
                "_googlecast._tcp.local",
                "Chromecast-bbb",
                8009,
                md="Google TV Streamer",
                fn="Bureau",
            ),
            mdns(GTV_IP, "_androidtvremote2._tcp.local", "Bureau", 6466),
            mdns(
                CC_IP,
                "_googlecast._tcp.local",
                "Chromecast-ccc",
                8009,
                md="Chromecast",
                fn="Accueil",
            ),
        ],
        ssdp=[
            (
                ssdp(SAM_IP, SAMSUNG, "SHP, UPnP/1.0, Samsung UPnP SDK/1.0"),
                SsdpDescription(
                    manufacturer="Samsung Electronics",
                    model_name="QE55Q60B",
                    friendly_name="[TV] Samsung Q60",
                ),
            ),
            (ssdp(SAM_IP, DIAL, "SHP, UPnP/1.0", 8001), None),
            (ssdp(LG_IP, DIAL, "Linux/4.4 UPnP/1.0 webOS", 1), None),
            (
                ssdp(LG_IP, RENDERER, "WebOS/4.1.0 UPnP/1.0", 1),
                SsdpDescription(
                    manufacturer="LG Electronics",
                    model_name="OLED55C2",
                    friendly_name="[LG] webOS TV",
                ),
            ),
        ],
        ports={
            TCL_IP: {5555, 8008, 8009},
            GTV_IP: {5555, 8008, 8009},
            SAM_IP: {8001, 8002, 9197},
            LG_IP: {3000, 3001},
            CC_IP: {8008, 8009},
        },
    )


async def test_tcl_android_tv() -> None:
    d = (await run(full_net()))[TCL_IP]
    assert (d.platform, d.model, d.name, d.mac) == (
        "google_tv",
        "Google TV",
        "TCL Salon",
        ARP[TCL_IP],
    )
    assert {"adb", "cast"} <= d.protocols
    assert d.suggested_drivers == ("power.adb", "power.wol")


async def test_google_tv_streamer() -> None:
    d = (await run(full_net()))[GTV_IP]
    assert d.platform == "google_tv" and d.model == "Google TV Streamer" and d.vendor == "Google"
    assert d.suggested_drivers[0] == "power.adb"


async def test_samsung_tizen() -> None:
    d = (await run(full_net()))[SAM_IP]
    assert (d.vendor, d.model, d.platform) == ("Samsung Electronics", "QE55Q60B", "tizen")
    assert d.suggested_drivers == ("power.samsung-ws", "power.wol")
    assert {"samsung_ws", "dial"} <= d.protocols and d.confidence == "high"


async def test_lg_webos() -> None:
    d = (await run(full_net()))[LG_IP]
    assert (d.vendor, d.model, d.platform) == ("LG Electronics", "OLED55C2", "webos")
    assert d.suggested_drivers == ("power.wol",)


async def test_chromecast_has_no_power_driver() -> None:
    d = (await run(full_net()))[CC_IP]
    assert d.platform == "cast" and d.model == "Chromecast" and d.suggested_drivers == ()


async def test_printer_and_phone_never_reported() -> None:
    got = await run(full_net())
    assert PRN_IP not in got and PHONE_IP not in got
    assert set(got) == {TCL_IP, GTV_IP, SAM_IP, LG_IP, CC_IP}


async def test_probe_ports_disabled_skips_sweep() -> None:
    net = full_net()
    got = await run(net, probe_ports=False)
    assert net.sweeps == 0
    assert "adb" not in got[TCL_IP].protocols and got[TCL_IP].suggested_drivers == ("power.wol",)


async def test_open_port_alone_is_a_signal_without_mac() -> None:
    d = (await run(ReplayNet({}, ports={"192.168.1.40": {5555}})))["192.168.1.40"]
    assert d.suggested_drivers == ("power.adb",) and d.mac is None and d.platform == "android"


async def test_hostile_strings_are_bounded_models() -> None:
    # le runtime nettoie ; le modèle borné refuse toute chaîne hors contrat
    with pytest.raises(ValueError):
        SsdpDescription(friendly_name="x" * 200)


async def test_failing_source_does_not_lose_others() -> None:
    net = full_net()

    async def boom(*a: Any, **k: Any) -> AsyncIterator[Any]:
        raise OSError("x")
        yield

    net.mdns_browse = boom  # type: ignore[method-assign]
    got = await run(net)
    assert SAM_IP in got and TCL_IP in got  # TCL via ports seuls


def test_manifest_and_contract() -> None:
    m = load_manifest(MOD / "module.json")
    assert (m.kind, m.capabilities, m.permissions) == (
        "discovery",
        ["discovery.lan"],
        ["network.discover"],
    )
    check_discoverer(Driver(), m.capabilities)
    assert isinstance(Driver(), Discoverer)
    schema = json.loads((MOD / "config.schema.json").read_text())
    assert schema["properties"]["probe_ports"]["default"] is True
    assert make_ctx("discovery.lan").module_id == "discovery.lan"


def test_i18n_fr_en_same_keys() -> None:
    def keys(o: object, p: str = "") -> set[str]:
        if isinstance(o, dict):
            return {k for kk, v in o.items() for k in keys(v, f"{p}.{kk}")}
        return {p}

    fr, en = (json.loads((MOD / "i18n" / f"{x}.json").read_text()) for x in ("fr", "en"))
    assert keys(fr) == keys(en) and ".config.discovery_lan.probe_ports.title" in keys(fr)


# --- test d'architecture : aucun accès réseau dans le module ----------------------------------
FORBIDDEN = {"socket", "ssl", "httpx", "urllib", "http", "requests", "aiohttp", "websockets",
             "subprocess", "telnetlib", "ftplib", "smtplib", "selectors", "asyncssh"}  # fmt: skip


def test_module_has_no_network_import() -> None:
    files = list((MOD / "agent").glob("*.py"))
    assert files
    for f in files:
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0:
                names = [n.module or ""]
            else:
                if isinstance(n, ast.Attribute) and n.attr in {
                    "open_connection",
                    "create_connection",
                    "create_datagram_endpoint",
                    "sock_connect",
                    "start_server",
                }:
                    raise AssertionError(f"{f.name}: {n.attr}")
                continue
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, f"{f.name}: import {name}"
