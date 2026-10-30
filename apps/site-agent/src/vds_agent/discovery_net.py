"""Façade réseau `DiscoveryNet` du runtime (vds-core 1.5) : seule voie réseau d'un module.

Chaque primitive applique elle-même les bornes : adresses dans le périmètre RFC1918 calculé par
l'agent, ports de `DISCOVERY_PORTS`, tailles, délais. Aucune cible ne vient du serveur ni du module.
Aucune connexion ADB / WebSocket / HTTP générique : le seul GET est la description SSDP (§6).
"""

import asyncio
import contextlib
import fcntl
import logging
import re
import socket
import struct
import xml.etree.ElementTree as ET  # noqa: S405 - DOCTYPE/ENTITY refusés avant analyse (_parse_xml)
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from vds_sdk.agent import (
    MdnsRecord,
    SsdpDescription,
    SsdpReply,
)
from vds_sdk.agent.discovery import DiscoveryScopeError, ensure_discovery_ports

log = logging.getLogger("vds.agent.discovery")

MDNS_GROUP = ("224.0.0.251", 5353)
SSDP_GROUP = ("239.255.255.250", 1900)
MAX_NETWORKS = 2
MIN_PREFIX = 22
CONNECT_TIMEOUT_S = 0.4
CONNECT_CONCURRENCY = 64
MAX_SWEEP_HOSTS = 1024
DESC_TIMEOUT_S = 2.0
DESC_MAX_BYTES = 64 * 1024
DESC_PER_SCAN = 32
EXCLUDED_IFACE_PREFIXES = (
    "docker", "br-", "veth", "virbr", "cni", "flannel", "tailscale", "wg", "tun", "tap",
)  # fmt: skip
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean(value: object, limit: int = 128) -> str | None:
    """Texte hostile -> chaîne sûre (contrôles retirés, 128 caractères)."""
    if value is None:
        return None
    s = _CONTROL.sub("", str(value)).strip()[:limit]
    return s or None


# --- périmètre --------------------------------------------------------------------------
@dataclass(frozen=True)
class Iface:
    name: str
    ip: IPv4Address
    prefix: int
    up: bool = True


def list_interfaces() -> list[Iface]:
    """Interfaces IPv4 (ioctl Linux, lecture seule ; aucun paquet émis)."""
    out: list[Iface] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _, name in socket.if_nameindex():
            req = struct.pack("256s", name.encode()[:15])
            try:
                flags = struct.unpack("H", fcntl.ioctl(s, 0x8913, req)[16:18])[0]
                addr = fcntl.ioctl(s, 0x8915, req)[20:24]
                mask = fcntl.ioctl(s, 0x891B, req)[20:24]
            except OSError:
                continue
            if flags & 0x8:  # IFF_LOOPBACK
                continue
            prefix = bin(int.from_bytes(mask, "big")).count("1")
            out.append(Iface(name, IPv4Address(addr), prefix, bool(flags & 0x1)))
    return out


@dataclass(frozen=True)
class Perimeter:
    networks: tuple[IPv4Network, ...]
    own: frozenset[IPv4Address]
    truncated: bool = False
    ignored: int = 0  # réseaux au-delà de MAX_NETWORKS

    def contains(self, ip: IPv4Address) -> bool:
        return ip not in self.own and any(
            ip in n and ip != n.network_address and ip != n.broadcast_address for n in self.networks
        )

    def ensure(self, ip: IPv4Address) -> IPv4Address:
        if not self.contains(ip):
            raise DiscoveryScopeError("discovery.out_of_scope")
        return ip

    def hosts(self) -> Iterator[IPv4Address]:
        seen: set[IPv4Address] = set()
        for n in self.networks:
            for h in n.hosts():
                if h not in self.own and h not in seen:
                    seen.add(h)
                    yield h


def compute_perimeter(ifaces: Iterable[Iface]) -> Perimeter | None:
    """§3 : RFC1918 des interfaces de l'agent, /22 max, 2 réseaux max. None = aucun réseau privé."""
    nets: list[IPv4Network] = []
    own: set[IPv4Address] = set()
    truncated = False
    ignored = 0
    for i in ifaces:
        if not i.up or i.name.startswith(EXCLUDED_IFACE_PREFIXES) or i.prefix >= 31:
            continue
        a = i.ip
        if (
            a.is_loopback
            or a.is_link_local
            or not any(
                a in ip_network(p) for p in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        ):
            continue
        own.add(a)
        net = ip_network(f"{a}/{i.prefix}", strict=False)
        if net.prefixlen < MIN_PREFIX:
            net = ip_network(f"{a}/{MIN_PREFIX}", strict=False)
            truncated = True
        if not isinstance(net, IPv4Network):
            continue
        if net in nets:
            continue
        if len(nets) >= MAX_NETWORKS:
            ignored += 1
            continue
        nets.append(net)
    if not nets:
        return None
    return Perimeter(tuple(nets), frozenset(own), truncated, ignored)


# --- DNS (mDNS) minimal ------------------------------------------------------------------
def build_mdns_query(services: Sequence[str]) -> bytes:
    """Requête PTR avec le bit QU (réponse unicast) ; aucun bind sur 5353."""
    out = struct.pack("!HHHHHH", 0, 0, len(services), 0, 0, 0)
    for svc in services:
        for label in svc.rstrip(".").split("."):
            out += bytes([len(label)]) + label.encode()
        out += b"\x00" + struct.pack("!HH", 12, 0x8001)
    return out


def _name(data: bytes, pos: int, depth: int = 0) -> tuple[str, int]:
    labels: list[str] = []
    end = -1
    while True:
        if pos >= len(data) or depth > 8:
            raise ValueError("dns")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if end < 0:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | data[pos + 1]
            depth += 1
            continue
        labels.append(data[pos + 1 : pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if end >= 0 else pos)


def parse_mdns(data: bytes) -> list[tuple[str, str, str, int, dict[str, str]]]:
    """-> [(service, instance, host, port, txt)] ; erreur de format => liste vide."""
    try:
        _, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        if not flags & 0x8000:
            return []
        pos = 12
        for _ in range(qd):
            _, pos = _name(data, pos)
            pos += 4
        ptr: list[tuple[str, str]] = []
        srv: dict[str, tuple[str, int]] = {}
        txt: dict[str, dict[str, str]] = {}
        for _ in range(min(an + ns + ar, 64)):
            name, pos = _name(data, pos)
            rtype, _, _, rdlen = struct.unpack("!HHIH", data[pos : pos + 10])
            pos += 10
            rd = data[pos : pos + rdlen]
            if rtype == 12:
                ptr.append((name, _name(data, pos)[0]))
            elif rtype == 33 and rdlen >= 6:
                port = struct.unpack("!H", rd[4:6])[0]
                srv[name] = (_name(data, pos + 6)[0], port)
            elif rtype == 16:
                txt[name] = _txt(rd)
            pos += rdlen
    except (ValueError, struct.error, IndexError):
        return []
    out = []
    for service, inst in ptr:
        host, port = srv.get(inst, ("", 0))
        out.append((service, inst, host, port, txt.get(inst, {})))
    return out


def _txt(rd: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(rd) and len(out) < 64:
        n = rd[i]
        item = rd[i + 1 : i + 1 + n].decode("utf-8", "replace")
        i += 1 + n
        k, _, v = item.partition("=")
        if k:
            out[k[:64]] = v
    total = 0
    for k, v in list(out.items()):
        total += len(k) + len(v)
        if total > 1024:
            del out[k]
    return out


def parse_ssdp(data: bytes, ip: IPv4Address) -> SsdpReply | None:
    try:
        head = data[:4096].decode("utf-8", "replace").split("\r\n")
    except ValueError:
        return None
    if not head or not head[0].upper().startswith("HTTP/1.1 200"):
        return None
    h = {}
    for line in head[1:]:
        k, _, v = line.partition(":")
        h[k.strip().lower()] = v.strip()
    st = clean(h.get("st"))
    if not st:
        return None
    loc = h.get("location")
    try:
        return SsdpReply(
            ip=ip, st=st, server=clean(h.get("server")), location=loc[:512] if loc else None
        )
    except ValidationError:
        return None


def _parse_xml(data: bytes) -> ET.Element | None:
    """Pas de DTD ni d'entités (équivalent de defusedxml.forbid_dtd/forbid_entities)."""
    low = data[:DESC_MAX_BYTES].lower()
    if b"<!doctype" in low or b"<!entity" in low:
        return None
    try:
        return ET.fromstring(data)  # noqa: S314
    except ET.ParseError:
        return None


def parse_description(data: bytes) -> SsdpDescription | None:
    root = _parse_xml(data)
    if root is None:
        return None
    vals: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("manufacturer", "modelName", "friendlyName", "deviceType") and tag not in vals:
            vals[tag] = el.text or ""
    return SsdpDescription(
        manufacturer=clean(vals.get("manufacturer")),
        model_name=clean(vals.get("modelName")),
        friendly_name=clean(vals.get("friendlyName")),
        device_type=clean(vals.get("deviceType")),
    )


# --- E/S injectables --------------------------------------------------------------------
UdpExchange = Callable[[list[bytes], tuple[str, int], float], AsyncIterator[tuple[bytes, str]]]
TcpProbe = Callable[[IPv4Address, int, float], Awaitable[bool]]


async def udp_exchange(
    payloads: list[bytes], dest: tuple[str, int], window_s: float
) -> AsyncIterator[tuple[bytes, str]]:
    """Émet les requêtes depuis un port éphémère puis écoute `window_s` secondes."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue(maxsize=512)

    class _P(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait((data[:9000], addr[0]))

    tr, _ = await loop.create_datagram_endpoint(
        _P,
        family=socket.AF_INET,
        local_addr=("0.0.0.0", 0),  # noqa: S104
    )
    try:
        sock = tr.get_extra_info("socket")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        for p in payloads:
            tr.sendto(p, dest)
        end = loop.time() + window_s
        while (left := end - loop.time()) > 0:
            try:
                yield await asyncio.wait_for(q.get(), left)
            except TimeoutError:
                break
    finally:
        tr.close()


async def tcp_probe(ip: IPv4Address, port: int, timeout_s: float) -> bool:
    """connect() puis fermeture immédiate : aucun octet écrit (ni CNXN ni invite RSA)."""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(str(ip), port), timeout_s)
    except (OSError, TimeoutError):
        return False
    w.close()
    with contextlib.suppress(OSError, TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(w.wait_closed(), 0.5)
    return True


class LanDiscoveryNet:
    def __init__(
        self,
        perimeter: Perimeter,
        *,
        deadline: float,
        arp_path: Path = Path("/proc/net/arp"),
        http: httpx.AsyncClient | None = None,
        udp: UdpExchange = udp_exchange,
        probe: TcpProbe = tcp_probe,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.p = perimeter
        self._deadline = deadline
        self._arp = arp_path
        self._http = http or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._own_http = http is None
        self._udp = udp
        self._probe = probe
        self._now = monotonic or asyncio.get_running_loop().time
        self._sem = asyncio.Semaphore(CONNECT_CONCURRENCY)
        self._described: set[IPv4Address] = set()
        self._desc_count = 0

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    def _window(self, w: float) -> float:
        return max(0.0, min(w, self._deadline - self._now()))

    # voisinage : lecture seule
    def neighbors(self) -> Mapping[IPv4Address, str]:
        out: dict[IPv4Address, str] = {}
        try:
            lines = self._arp.read_text().splitlines()[1:]
        except OSError:
            return out
        for line in lines:
            c = line.split()
            if (
                len(c) < 4
                or c[3] == "00:00:00:00:00:00"
                or not re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", c[3])
            ):
                continue
            try:
                ip = IPv4Address(c[0])
            except ValueError:
                continue
            if self.p.contains(ip):
                out[ip] = c[3].lower()
        return out

    async def mdns_browse(
        self, services: Sequence[str], window_s: float
    ) -> AsyncIterator[MdnsRecord]:
        svcs = [s for s in services if re.fullmatch(r"_[a-z0-9-]{1,32}\._(tcp|udp)\.local", s)]
        if len(svcs) != len(services):
            raise DiscoveryScopeError("discovery.service_invalid")
        async for data, src in self._udp(
            [build_mdns_query(svcs)], MDNS_GROUP, self._window(window_s)
        ):
            ip = self._src(src)
            if ip is None:
                continue
            for service, inst, host, port, txt in parse_mdns(data):
                if not port:
                    continue
                try:
                    yield MdnsRecord(
                        service=clean(service) or "", instance=clean(inst) or "",
                        host=clean(host) or "", ip=ip, port=port,
                        txt={clean(k) or "": clean(v) or "" for k, v in txt.items()},
                    )  # fmt: skip
                except ValidationError:
                    continue

    async def ssdp_search(
        self, targets: Sequence[str], window_s: float
    ) -> AsyncIterator[SsdpReply]:
        reqs = []
        for t in targets:
            if not re.fullmatch(r"urn:[A-Za-z0-9.:_-]{1,120}", t):
                raise DiscoveryScopeError("discovery.service_invalid")
            reqs.append(
                (
                    "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                    f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {t}\r\n\r\n'
                ).encode()
            )
        seen: set[tuple[IPv4Address, str]] = set()
        async for data, src in self._udp(reqs, SSDP_GROUP, self._window(window_s)):
            ip = self._src(src)
            reply = parse_ssdp(data, ip) if ip else None
            if reply and (reply.ip, reply.st) not in seen:
                seen.add((reply.ip, reply.st))
                yield reply

    def _src(self, src: str) -> IPv4Address | None:
        try:
            ip = ip_address(src)
        except ValueError:
            return None
        return ip if isinstance(ip, IPv4Address) and self.p.contains(ip) else None

    async def ssdp_description(self, reply: SsdpReply) -> SsdpDescription | None:
        """Un GET borné (§6) ; None si LOCATION suspecte ou réponse hors bornes."""
        if (
            not reply.location
            or reply.ip in self._described
            or self._desc_count >= DESC_PER_SCAN
            or not self.p.contains(reply.ip)
        ):
            return None
        try:
            u = urlsplit(reply.location)
            host = ip_address(u.hostname or "")  # nom DNS => ValueError
            port = u.port or 80
        except ValueError:
            return None
        if (
            u.scheme != "http" or u.username or u.password or host != reply.ip
            or not (port == 80 or port >= 1024)
        ):  # fmt: skip
            return None
        self._described.add(reply.ip)
        self._desc_count += 1
        timeout = min(DESC_TIMEOUT_S, self._window(DESC_TIMEOUT_S))
        if timeout <= 0:
            return None
        url = f"http://{reply.ip}:{port}{u.path or '/'}" + (f"?{u.query}" if u.query else "")
        try:
            async with (
                asyncio.timeout(timeout),
                self._http.stream("GET", url, headers={"Accept": "text/xml"}, timeout=timeout) as r,
            ):
                if r.status_code != 200:  # redirection comprise : jamais suivie
                    return None
                try:
                    if int(r.headers.get("content-length", "0")) > DESC_MAX_BYTES:
                        return None
                except ValueError:
                    return None
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > DESC_MAX_BYTES:
                        return None
        except (httpx.HTTPError, TimeoutError, OSError):
            return None
        return parse_description(buf)

    async def _one(self, ip: IPv4Address, port: int) -> bool:
        async with self._sem:
            return await self._probe(ip, port, CONNECT_TIMEOUT_S)

    async def tcp_open(self, ip: IPv4Address, ports: Sequence[int]) -> frozenset[int]:
        ensure_discovery_ports(ports)
        self.p.ensure(ip)
        res = await asyncio.gather(*(self._one(ip, p) for p in ports))
        return frozenset(p for p, ok in zip(ports, res, strict=True) if ok)

    async def sweep(
        self, ports: Sequence[int]
    ) -> AsyncIterator[tuple[IPv4Address, frozenset[int]]]:
        ensure_discovery_ports(ports)
        hosts = []
        for h in self.p.hosts():
            hosts.append(h)
            if len(hosts) >= MAX_SWEEP_HOSTS:
                break

        async def one(h: IPv4Address) -> tuple[IPv4Address, frozenset[int]]:
            return h, await self.tcp_open(h, ports)

        tasks = [asyncio.ensure_future(one(h)) for h in hosts]
        try:
            for fut in asyncio.as_completed(tasks):
                left = self._deadline - self._now()
                if left <= 0:
                    break
                try:
                    ip, open_ports = await asyncio.wait_for(fut, left)
                except TimeoutError:
                    break
                if open_ports:
                    yield ip, open_ports
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
