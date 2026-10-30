"""Empreintes : preuves collectées pour une IP -> `DiscoveredDevice` (None sans signal TV)."""

from dataclasses import dataclass, field
from ipaddress import IPv4Address

from vds_sdk.agent import DISCOVERY_PORTS, DiscoveredDevice, MdnsRecord, SsdpDescription, SsdpReply

MDNS_SERVICES = [
    "_googlecast._tcp.local",
    "_androidtvremote2._tcp.local",
    "_adb-tls-connect._tcp.local",
    "_airplay._tcp.local",
]
SSDP_DIAL = "urn:dial-multiscreen-org:service:dial:1"
SSDP_SAMSUNG = "urn:samsung.com:device:RemoteControlReceiver:1"
SSDP_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
SSDP_TARGETS = [SSDP_DIAL, SSDP_SAMSUNG, SSDP_RENDERER]

PORT_PROTOCOL = {
    5555: "adb",
    8001: "samsung_ws",
    8002: "samsung_ws",
    8008: "cast",
    8009: "cast",
    3000: "webos",
    3001: "webos",
    9197: "dial",
}
assert set(PORT_PROTOCOL) == set(DISCOVERY_PORTS)  # noqa: S101 - cohérence avec la liste fermée

# Marque reconnue dans un texte libre (fabricant SSDP, SERVER, modèle, nom).
VENDOR_WORDS = (
    ("samsung", "Samsung"),
    ("webos", "LG"),
    ("lg electronics", "LG"),
    ("tcl", "TCL"),
    ("sony", "Sony"),
    ("philips", "Philips"),
    ("hisense", "Hisense"),
    ("google", "Google"),
)


@dataclass
class Evidence:
    mdns: list[MdnsRecord] = field(default_factory=list)
    ssdp: list[SsdpReply] = field(default_factory=list)
    descriptions: list[SsdpDescription] = field(default_factory=list)
    ports: frozenset[int] = frozenset()


def _txt(rec: MdnsRecord, key: str) -> str | None:
    v = rec.txt.get(key)
    return v or None


def _vendor(texts: list[str]) -> str | None:
    blob = " ".join(texts).lower()
    for word, vendor in VENDOR_WORDS:
        if word in blob:
            return vendor
    return None


def to_device(ip: IPv4Address, e: Evidence, mac: str | None) -> DiscoveredDevice | None:
    services = {r.service.split(".")[0] for r in e.mdns}
    sts = {r.st for r in e.ssdp}
    protocols: set[str] = {PORT_PROTOCOL[p] for p in e.ports if p in PORT_PROTOCOL}
    if "_googlecast" in services:
        protocols.add("cast")
    if "_adb-tls-connect" in services:
        protocols.add("adb")
    if "_airplay" in services:
        protocols.add("airplay")
    if SSDP_DIAL in sts:
        protocols.add("dial")
    if SSDP_SAMSUNG in sts:
        protocols.add("samsung_ws")
    has_signal = bool(e.ports or services or sts)
    if not has_signal:  # ARP seul (téléphone, PC, imprimante) : ne sort pas de l'agent
        return None

    model = next((m for r in e.mdns if (m := _txt(r, "md"))), None) or next(
        (d.model_name for d in e.descriptions if d.model_name), None
    )
    name = (
        next((n for r in e.mdns if (n := _txt(r, "fn"))), None)
        or next((d.friendly_name for d in e.descriptions if d.friendly_name), None)
        or next((r.instance.split("._")[0] for r in e.mdns if r.instance), None)
    )
    manufacturer = next((d.manufacturer for d in e.descriptions if d.manufacturer), None)
    texts = [
        t
        for t in (
            manufacturer,
            model,
            name,
            *[r.server for r in e.ssdp],
            *[d.device_type for d in e.descriptions],
        )
        if t
    ]
    vendor = manufacturer or _vendor(texts)
    low = " ".join(texts).lower()

    tizen = bool(protocols & {"samsung_ws"}) or "samsung" in low
    webos = "webos" in protocols or "webos" in low or (vendor or "").upper() == "LG"
    androidtv = "_androidtvremote2" in services or "adb" in protocols
    google_tv = androidtv and ("google tv" in low or "googletv" in low)

    platform: str = "unknown"
    drivers: list[str] = []
    if tizen:
        platform = "tizen"
        drivers = ["power.samsung-ws"]
    elif webos:
        platform = "webos"
    elif androidtv:
        platform = "google_tv" if google_tv else "android"
        if "adb" in protocols:
            drivers = ["power.adb"]
    elif "cast" in protocols:
        platform = "cast"
    if mac and platform in ("tizen", "webos", "android", "google_tv"):
        drivers.append("power.wol")

    sources = sum(
        bool(x) for x in (e.ports, services, sts)
    )  # sources indépendantes (ports, mDNS, SSDP)
    if platform == "unknown" or protocols <= {"dial", "airplay"} and platform != "tizen":
        confidence = "low"
    elif sources >= 2 and (vendor or model):
        confidence = "high"
    else:
        confidence = "medium" if protocols else "low"

    return DiscoveredDevice(
        ip=ip,
        mac=mac,
        vendor=vendor,
        model=model,
        name=name,
        platform=platform,  # type: ignore[arg-type]
        protocols=frozenset(protocols),  # type: ignore[arg-type]
        suggested_drivers=tuple(drivers[:4]),
        confidence=confidence,  # type: ignore[arg-type]
    )
