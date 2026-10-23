"""Paquet magique Wake-on-LAN et émetteur UDP injectable."""

import asyncio
import re
import socket
from typing import Protocol

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(mac: str) -> bytes:
    flat = re.sub(r"[:\-.\s]", "", mac.lower())
    if not _MAC_RE.match(flat):
        raise ValueError("power.wol.mac_invalid")
    return bytes.fromhex(flat)


def magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + normalize_mac(mac) * 16


class UdpSender(Protocol):
    async def send(self, packet: bytes, broadcast: str, port: int) -> None: ...


class SocketUdpSender:
    async def send(self, packet: bytes, broadcast: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setblocking(False)
            await loop.sock_sendto(s, packet, (broadcast, port))
