"""Façade DiscoveryNet et runtime de scan (D2) : uniquement des faux, aucune connexion réelle."""

import asyncio
import struct
from collections.abc import AsyncIterator
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from vds_agent import discovery_net as dn
from vds_agent.discovery import DiscoveryRunner
from vds_agent.discovery_net import Iface, LanDiscoveryNet, Perimeter, compute_perimeter
from vds_sdk.agent import DiscoveredDevice, DiscoveryScopeError, SsdpReply

A = IPv4Address


def ifc(name: str, ip: str, prefix: int, up: bool = True) -> Iface:
    return Iface(name, A(ip), prefix, up)


# --- périmètre ---------------------------------------------------------------------------
def test_perimeter_slash16_truncated_to_22() -> None:
    p = compute_perimeter([ifc("eth0", "192.168.5.20", 16)])
    assert p
    assert p.networks == (IPv4Network("192.168.4.0/22"),)
    assert p.truncated


def test_perimeter_excludes_docker_cgnat_loopback_public_linklocal() -> None:
    ifaces = [
        ifc("docker0", "172.17.0.1", 16),
        ifc("br-abc", "172.18.0.1", 16),
        ifc("tailscale0", "10.9.0.1", 24),
        ifc("eth1", "100.64.1.5", 24),
        ifc("eth2", "8.8.8.8", 24),
        ifc("eth3", "169.254.3.3", 16),
        ifc("eth4", "127.0.0.1", 8),
        ifc("eth5", "192.168.1.5", 24, up=False),
    ]
    assert compute_perimeter(ifaces) is None


def test_perimeter_max_two_networks_and_own_ip_removed() -> None:
    p = compute_perimeter(
        [ifc("eth0", "192.168.1.5", 24), ifc("eth1", "10.0.0.7", 24), ifc("eth2", "172.16.0.9", 24)]
    )
    assert p
    assert len(p.networks) == 2 and p.ignored == 1
    assert A("192.168.1.5") not in set(p.hosts())
    assert not p.contains(A("192.168.1.5"))
    assert not p.contains(A("192.168.1.0")) and not p.contains(A("192.168.1.255"))
    assert p.contains(A("192.168.1.6"))


@pytest.mark.parametrize("ip", ["8.8.8.8", "127.0.0.1", "169.254.1.1", "100.64.0.9", "192.168.2.9"])
def test_perimeter_rejects_public_loopback_and_other(ip: str) -> None:
    p = compute_perimeter([ifc("eth0", "192.168.1.5", 24)])
    assert p
    assert not p.contains(A(ip))
    with pytest.raises(DiscoveryScopeError):
        p.ensure(A(ip))


# --- façade ------------------------------------------------------------------------------
PER = Perimeter((IPv4Network("192.168.1.0/24"),), frozenset({A("192.168.1.5")}))


def make_net(**kw: Any) -> LanDiscoveryNet:
    return LanDiscoveryNet(PER, deadline=asyncio.get_running_loop().time() + 30, **kw)


async def test_tcp_open_rejects_port_and_targets() -> None:
    probed: list[tuple[str, int]] = []

    async def probe(ip: IPv4Address, port: int, t: float) -> bool:
        probed.append((str(ip), port))
        return port == 5555

    net = make_net(probe=probe)
    assert await net.tcp_open(A("192.168.1.9"), [5555, 8002]) == {5555}
    for ip, ports in [("192.168.1.9", [22]), ("8.8.8.8", [5555]), ("127.0.0.1", [5555])]:
        with pytest.raises(DiscoveryScopeError):
            await net.tcp_open(A(ip), ports)
    with pytest.raises(DiscoveryScopeError):
        async for _ in net.sweep([22]):
            pass
    assert all(ip == "192.168.1.9" for ip, _ in probed)


async def test_tcp_probe_sends_zero_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[bytes] = []
    closed: list[bool] = []

    class W:
        def write(self, b: bytes) -> None:
            writes.append(b)

        def writelines(self, b: Any) -> None:
            writes.append(b"x")

        def close(self) -> None:
            closed.append(True)

        async def wait_closed(self) -> None:
            return None

    async def fake_open(host: str, port: int) -> tuple[object, W]:
        return object(), W()

    monkeypatch.setattr(asyncio, "open_connection", fake_open)
    assert await dn.tcp_probe(A("192.168.1.9"), 5555, 0.4)
    assert writes == [] and closed == [True]


async def test_sweep_yields_only_open_hosts() -> None:
    async def probe(ip: IPv4Address, port: int, t: float) -> bool:
        return str(ip) == "192.168.1.77" and port == 8002

    net = make_net(probe=probe)
    got = [x async for x in net.sweep([5555, 8002])]
    assert got == [(A("192.168.1.77"), frozenset({8002}))]


def desc_net(handler: Any) -> LanDiscoveryNet:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    net = make_net(http=http)
    net._seen.update(A(f"192.168.1.{i}") for i in range(2, 100))  # corroborées (M1)
    return net


XML = (
    b'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><deviceType>x</deviceType>'
    b"<friendlyName>Salon</friendlyName><manufacturer>Samsung</manufacturer>"
    b"<modelName>QE55</modelName></device></root>"
)


def reply(loc: str | None, ip: str = "192.168.1.20") -> SsdpReply:
    return SsdpReply(ip=A(ip), st="urn:x", location=loc)


async def test_ssdp_description_ok_and_sanitized() -> None:
    urls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        urls.append(str(req.url))
        return httpx.Response(200, content=XML)

    d = await desc_net(h).ssdp_description(reply("http://192.168.1.20:9197/dmr"))
    assert d and d.manufacturer == "Samsung" and d.friendly_name == "Salon"
    assert urls == ["http://192.168.1.20:9197/dmr"]


@pytest.mark.parametrize(
    "loc",
    [
        "http://192.168.1.21:9197/x",  # autre IP du LAN
        "http://8.8.8.8/x",  # public
        "http://tv.local:9197/x",  # nom DNS
        "http://127.0.0.1:9197/x",
        "http://192.168.1.20:22/x",  # port privilégié
        "http://192.168.1.20:8080/x",  # hors liste UPnP
        "http://192.168.1.20:9197/x?action=reboot",  # query
        "http://192.168.1.20:9197/" + "a" * 300,  # chemin trop long
        "https://192.168.1.20:9197/x",
        "http://u:p@192.168.1.20:9197/x",
        "file:///etc/passwd",
        None,
    ],
)
async def test_ssdp_description_rejects_suspicious_location(loc: str | None) -> None:
    called: list[int] = []

    def h(req: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, content=XML)

    assert await desc_net(h).ssdp_description(reply(loc)) is None
    assert called == []


async def test_ssdp_description_redirect_oversize_dtd_ignored() -> None:
    loc = "http://192.168.1.20:9197/x"
    redirect = lambda r: httpx.Response(302, headers={"location": "http://192.168.1.99/"})  # noqa: E731
    big = lambda r: httpx.Response(200, content=b"<a>" + b"x" * 65 * 1024 + b"</a>")  # noqa: E731
    big_len = lambda r: httpx.Response(  # noqa: E731
        200, content=XML, headers={"content-length": str(65 * 1024)}
    )
    dtd = lambda r: httpx.Response(  # noqa: E731
        200,
        content=b'<!DOCTYPE r [<!ENTITY a "bb">]><root><device><friendlyName>&a;</friendlyName>'
        b"</device></root>",
    )
    for handler in (redirect, big, big_len, dtd):
        assert await desc_net(handler).ssdp_description(reply(loc)) is None


async def test_ssdp_description_once_per_ip_and_32_per_scan() -> None:
    net = desc_net(lambda r: httpx.Response(200, content=XML))
    assert await net.ssdp_description(reply("http://192.168.1.20:9197/x"))
    assert await net.ssdp_description(reply("http://192.168.1.20:9197/x")) is None
    n = 1
    for i in range(30, 70):
        if await net.ssdp_description(reply(f"http://192.168.1.{i}:9197/x", f"192.168.1.{i}")):
            n += 1
    assert n == 32


async def test_ssdp_search_filters_sources_and_dedups() -> None:
    ok = b"HTTP/1.1 200 OK\r\nST: urn:x\r\nLOCATION: http://192.168.1.20:80/d\r\nSERVER: a\x01\r\n\r\n"  # noqa: E501

    async def udp(
        p: list[bytes], dest: tuple[str, int], w: float
    ) -> AsyncIterator[tuple[bytes, str]]:
        assert dest == dn.SSDP_GROUP and b"M-SEARCH" in p[0]
        for src in ("192.168.1.20", "192.168.1.20", "8.8.8.8", "127.0.0.1", "192.168.1.5"):
            yield ok, src

    got = [r async for r in make_net(udp=udp).ssdp_search(["urn:x"], 1)]
    assert [str(r.ip) for r in got] == ["192.168.1.20"] and got[0].server == "a"
    with pytest.raises(DiscoveryScopeError):
        _ = [r async for r in make_net(udp=udp).ssdp_search(["x\r\nHOST: evil"], 1)]


def _name(n: str) -> bytes:
    return b"".join(bytes([len(x)]) + x.encode() for x in n.split(".")) + b"\x00"


def _rr(name: str, t: int, rd: bytes) -> bytes:
    return _name(name) + struct.pack("!HHIH", t, 1, 120, len(rd)) + rd


async def test_mdns_browse_parses_and_checks_source() -> None:
    svc, inst = "_googlecast._tcp.local", "Chromecast-abc._googlecast._tcp.local"
    txt = b"".join(bytes([len(x)]) + x for x in (b"md=Chromecast", b"fn=Salon"))
    pkt = (
        struct.pack("!HHHHHH", 0, 0x8400, 0, 3, 0, 0)
        + _rr(svc, 12, _name(inst))
        + _rr(inst, 33, struct.pack("!HHH", 0, 0, 8009) + _name("cc.local"))
        + _rr(inst, 16, txt)
    )
    assert dn.build_mdns_query([svc])[-2:] == struct.pack("!H", 0x8001)

    async def udp(
        p: list[bytes], dest: tuple[str, int], w: float
    ) -> AsyncIterator[tuple[bytes, str]]:
        assert dest == dn.MDNS_GROUP
        yield pkt, "192.168.1.30"
        yield pkt, "8.8.8.8"
        yield b"\xff" * 5, "192.168.1.31"  # paquet malformé

    got = [r async for r in make_net(udp=udp).mdns_browse([svc], 1)]
    assert len(got) == 1
    assert got[0].ip == A("192.168.1.30") and got[0].port == 8009
    assert got[0].txt["md"] == "Chromecast" and got[0].host == "cc.local"
    with pytest.raises(DiscoveryScopeError):
        _ = [r async for r in make_net(udp=udp).mdns_browse(["evil"], 1)]


async def test_neighbors_filters_perimeter(tmp_path: Path) -> None:
    arp = tmp_path / "arp"
    arp.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "192.168.1.20 0x1 0x2 AA:BB:CC:00:11:22 * eth0\n"
        "192.168.1.21 0x1 0x2 00:00:00:00:00:00 * eth0\n"
        "10.1.1.1 0x1 0x2 aa:bb:cc:00:11:33 * eth0\n"
        "192.168.1.22 0x1 0x2 garbage * eth0\n"
    )
    assert make_net(arp_path=arp).neighbors() == {A("192.168.1.20"): "aa:bb:cc:00:11:22"}


# --- runner ------------------------------------------------------------------------------
class FakeModule:
    scopes = frozenset({"lan"})

    def __init__(self, devs: list[DiscoveredDevice], hang: bool = False) -> None:
        self.devs, self.hang, self.started = devs, hang, asyncio.Event()

    async def discover(self, scope: Any, net: Any, report: Any) -> None:
        self.started.set()
        for d in self.devs:
            await report(d)
        if self.hang:
            await asyncio.sleep(60)


def runner(
    module: Any,
    frames: list[tuple[str, dict[str, Any]]],
    ifaces: list[Iface] | None = None,
    **kw: Any,
) -> DiscoveryRunner:
    async def emit(name: str, payload: dict[str, Any]) -> None:
        frames.append((name, payload))

    return DiscoveryRunner(
        provider=lambda: module,
        active_drivers=lambda: {"power.adb", "power.wol"},
        emit=emit,
        interfaces=lambda: ifaces if ifaces is not None else [ifc("eth0", "192.168.1.5", 24)],
        net_factory=lambda p, dl: make_net(),
        batch_interval_s=0.01,
        **kw,
    )


def dev(ip: str, **kw: Any) -> DiscoveredDevice:
    return DiscoveredDevice(ip=A(ip), **kw)


ARGS = {"scan_id": uuid4(), "scope": "lan", "probe_ports": True}


async def test_runner_merge_filter_batches_and_result() -> None:
    frames: list[tuple[str, dict[str, Any]]] = []
    m = FakeModule(
        [
            dev("192.168.1.20", mac="aa:bb:cc:00:11:22", protocols=frozenset({"adb"}),
                suggested_drivers=("power.adb", "power.nope")),
            dev("192.168.1.20", vendor="TCL", protocols=frozenset({"cast"}), confidence="high"),
            dev("192.168.1.21", protocols=frozenset({"dial"})),
            dev("8.8.8.8", protocols=frozenset({"dial"})),  # hors périmètre
            dev("192.168.1.5"),  # l'agent lui-même
        ]
    )  # fmt: skip
    status, code, data = await runner(m, frames).run(ARGS)
    assert (status, code) == ("ok", None)
    assert data["device_count"] == 2 and data["capped"] is False
    assert data["networks"] == ["192.168.1.0/24"] and data["truncated"] is False
    sent = {d["ip"]: d for _, p in frames for d in p["devices"]}
    assert set(sent) == {"192.168.1.20", "192.168.1.21"}
    d20 = sent["192.168.1.20"]
    assert d20["vendor"] == "TCL" and sorted(d20["protocols"]) == ["adb", "cast"]
    assert d20["suggested_drivers"] == ["power.adb"] and d20["confidence"] == "high"
    assert all(n == "discovery.batch" and len(p["devices"]) <= 16 for n, p in frames)


async def test_runner_cap_128() -> None:
    frames: list[tuple[str, dict[str, Any]]] = []
    ifaces = [ifc("eth0", "10.0.0.1", 22)]
    m = FakeModule([dev(f"10.0.{i // 250}.{i % 250 + 2}") for i in range(140)])
    _, _, data = await runner(m, frames, ifaces).run(ARGS)
    assert data["device_count"] == 128 and data["capped"] is True
    assert sum(len(p["devices"]) for _, p in frames) == 128
    assert all(len(p["devices"]) <= 16 for _, p in frames)
    assert [p["seq"] for _, p in frames] == list(range(len(frames)))


async def test_runner_failures(caplog: pytest.LogCaptureFixture) -> None:
    frames: list[tuple[str, dict[str, Any]]] = []
    assert (await runner(None, frames).run(ARGS))[1] == "discovery.no_provider"
    pub = [ifc("eth0", "8.8.8.8", 24)]
    assert (await runner(FakeModule([]), frames, pub).run(ARGS))[
        1
    ] == "discovery.no_private_network"

    class Boom:
        scopes = frozenset({"lan"})

        async def discover(self, scope: Any, net: Any, report: Any) -> None:
            raise RuntimeError("x")

    assert (await runner(Boom(), frames).run(ARGS))[1] == "discovery.internal"

    class Leaky:
        scopes = frozenset({"lan"})

        async def discover(self, scope: Any, net: Any, report: Any) -> None:
            raise ValueError("192.168.1.20 aa:bb:cc:dd:ee:ff")

    with caplog.at_level("DEBUG"):
        assert (await runner(Leaky(), frames).run(ARGS))[1] == "discovery.internal"
    assert "192.168.1.20" not in caplog.text and "aa:bb" not in caplog.text


async def test_runner_second_scan_is_busy_and_deadline_cuts() -> None:
    frames: list[tuple[str, dict[str, Any]]] = []
    m = FakeModule([dev("192.168.1.20", protocols=frozenset({"dial"}))], hang=True)
    r = runner(m, frames, budget_s=0.3)
    first = asyncio.ensure_future(r.run(ARGS))
    await m.started.wait()
    assert (await r.run(ARGS)) == ("failed", "discovery.busy", {})
    status, _, data = await first
    assert status == "ok" and data["device_count"] == 1  # coupé à l'échéance, partiel conservé
    assert (await r.run(ARGS))[0] == "ok"  # verrou relâché


def test_clean_strips_controls_and_truncates() -> None:
    assert dn.clean("a\x07b\n" + "x" * 300) == "ab" + "x" * 126
    assert dn.clean("\x00") is None


async def test_agent_loads_discovery_lan_and_routes_scan(tmp_path: Path) -> None:
    from vds_agent.cascade import Cascade
    from vds_agent.modules import discover
    from vds_agent.runtime import Agent
    from vds_agent.settings import AgentSettings
    from vds_agent.store import Store
    from vds_agent.testing import module_dir
    from vds_sdk.wire import Cmd

    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "discovery.lan").symlink_to(module_dir("discovery.lan"))
    s = AgentSettings(
        server_url="https://x.invalid",
        token="vds_agt_" + "x" * 43,  # noqa: S106
        data_dir=tmp_path / "d",
        modules_dir=mods,
    )
    agent = Agent(s, Store(s.data_dir), cascade=Cascade(), http=httpx.AsyncClient())
    agent.discovered = discover(mods)
    assert agent.discovered and agent.discovered[0].error is None
    await agent._activate([{"id": "discovery.lan", "version": "0.1.0"}])
    assert agent._discoverer() is not None

    agent.discovery._ifaces = lambda: [ifc("eth0", "192.168.1.5", 24)]  # type: ignore[assignment]
    cmd = Cmd.model_validate(
        {"cmd_id": str(uuid4()), "name": "discovery.scan", "screen_id": None,
         "args": {"scan_id": str(uuid4()), "probe_ports": False},
         "expires_at": "2099-01-01T00:00:00Z"}
    )  # fmt: skip
    args = cmd.validate_for("agent").model_dump()
    # réseau entièrement simulé : aucun envoi UDP ni connexion (udp/probe factices dans make_net)
    agent.discovery._net_factory = lambda p, dl: make_net(udp=_silent_udp, probe=_closed_probe)  # type: ignore[assignment]
    status, code, data = await agent.dispatch(cmd, args)
    assert (status, code) == ("ok", None) and data["device_count"] == 0
    await agent.close()


async def _silent_udp(
    p: list[bytes], dest: tuple[str, int], w: float
) -> AsyncIterator[tuple[bytes, str]]:
    return
    yield


async def _closed_probe(ip: IPv4Address, port: int, t: float) -> bool:
    return False


async def test_ssdp_description_requires_corroboration(tmp_path: Path) -> None:
    called: list[int] = []

    def h(req: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, content=XML)

    arp = tmp_path / "arp"
    arp.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "192.168.1.30 0x1 0x2 aa:bb:cc:dd:ee:ff * eth0\n"
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(h), follow_redirects=False)
    net = make_net(http=http, arp_path=arp)
    # IP usurpée jamais vue ailleurs : aucun GET
    assert await net.ssdp_description(reply("http://192.168.1.20:9197/d")) is None
    assert called == []
    # vue dans l'ARP : GET autorisé
    assert await net.ssdp_description(reply("http://192.168.1.30:9197/d", "192.168.1.30"))
    assert len(called) == 1


async def test_ssdp_spoofed_query_off_list_port_no_get() -> None:
    called: list[int] = []

    def h(req: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, content=XML)

    net = desc_net(h)
    assert await net.ssdp_description(reply("http://192.168.1.20:8080/cgi?action=x")) is None
    assert called == []


@pytest.mark.parametrize("enc", ["utf-16", "utf-32", "utf-16-le"])
def test_parse_xml_rejects_non_utf8(enc: str) -> None:
    doc = '<!DOCTYPE r [<!ENTITY a "bb">]><root><friendlyName>&a;</friendlyName></root>'
    assert dn._parse_xml(doc.encode(enc)) is None
    assert dn._parse_xml("<root/>".encode(enc)) is None
    assert dn._parse_xml(b"<root/>") is not None


async def test_facade_respects_probe_ports_false() -> None:
    probed: list[int] = []

    async def probe(ip: IPv4Address, port: int, t: float) -> bool:
        probed.append(port)
        return True

    net = make_net(probe=probe, probe_ports=False)
    with pytest.raises(DiscoveryScopeError):
        await net.tcp_open(A("192.168.1.9"), [5555])
    with pytest.raises(DiscoveryScopeError):
        async for _ in net.sweep([5555]):
            pass
    assert probed == []


def test_clean_strips_bidi_and_zero_width() -> None:
    assert dn.clean("Sam\u202esung\u200b \u2066QM\ufeff55") == "Samsung QM55"
