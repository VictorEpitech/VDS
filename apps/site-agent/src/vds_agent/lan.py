"""Service LAN du cache (opt-in, désactivé par défaut) : GET/HEAD `/m/<sha256>` sur URL signée.

Seul port en écoute de l'agent. Aucune autre route, aucun listing, pairs hors réseau privé refusés.
Signature : HMAC-SHA256(clé, "<sha256>\\n<exp>\\n<kid>") en hexadécimal.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

from .cache import SHA_RE, MediaCache

log = logging.getLogger("vds.agent.lan")

MAX_HEAD = 8192
READ_TIMEOUT_S = 10.0
MAX_CONNECTIONS = 32
MAX_TTL_S = 3 * 86400  # une URL ne peut pas être valable plus de J+1 ; plafond défensif
CHUNK = 1 << 16


def sign(key: bytes, sha256: str, exp: int, kid: str) -> str:
    return hmac.new(key, f"{sha256}\n{exp}\n{kid}".encode(), hashlib.sha256).hexdigest()


def signed_path(key: bytes, sha256: str, exp: int, kid: str) -> str:
    return f"/m/{sha256}?exp={exp}&kid={kid}&sig={sign(key, sha256, exp, kid)}"


def parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("agent.cache_lan_listen_invalid")
    return host, int(port)


def is_lan_peer(address: str) -> bool:
    ip = ipaddress.ip_address(address.partition("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:a.b.c.d évalué comme l'IPv4 sous-jacente
    return ip.is_private or ip.is_loopback or ip.is_link_local


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """(début, fin incluse), None sans Range, 'invalid' si non satisfaisable (un intervalle)."""
    if not header:
        return None
    if not header.startswith("bytes=") or "," in header:
        return "invalid"
    a, _, b = header[6:].partition("-")
    try:
        if a == "":
            n = int(b)
            if n <= 0:
                return "invalid"
            return max(0, size - n), size - 1
        start = int(a)
        end = min(int(b), size - 1) if b else size - 1
    except ValueError:
        return "invalid"
    return (start, end) if 0 <= start <= end < size else "invalid"


class LanServer:
    def __init__(
        self,
        cache: MediaCache,
        listen: str,
        keys: Callable[[], tuple[str, bytes] | None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache = cache
        self.host, self.port = parse_listen(listen)
        self._keys = keys  # (kid, clé) courants, ou None tant que le serveur ne l'a pas fournie
        self._clock = clock
        self._srv: asyncio.Server | None = None
        self._slots = asyncio.Semaphore(MAX_CONNECTIONS)

    async def start(self) -> int:
        self._srv = await asyncio.start_server(self._handle, self.host, self.port)
        return int(self._srv.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._srv:
            self._srv.close()
            await self._srv.wait_closed()

    # --- HTTP minimal ---------------------------------------------------------------
    async def _reply(
        self,
        w: asyncio.StreamWriter,
        status: int,
        reason: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        h = {"Content-Length": str(len(body)), "Connection": "close", "Cache-Control": "no-store"}
        h |= {"X-Content-Type-Options": "nosniff"} | (headers or {})
        head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
        w.write(head.encode() + b"\r\n" + body)
        await w.drain()

    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if self._slots.locked():  # plafond de connexions simultanées atteint
            w.close()
            return
        async with self._slots:
            await self._handle_one(r, w)

    async def _handle_one(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            peer = w.get_extra_info("peername")
            if not peer or not is_lan_peer(str(peer[0])):
                await self._reply(w, 403, "Forbidden")
                return
            raw = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), READ_TIMEOUT_S)
            await self._serve(w, raw)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass  # requête incomplète, trop longue ou trop lente : fermeture silencieuse
        except (ConnectionError, OSError):
            pass
        finally:
            w.close()

    def _authorize(self, sha: str, query: dict[str, list[str]]) -> bool:
        keys = self._keys()
        try:
            exp = int(query["exp"][0])
            kid, sig = query["kid"][0], query["sig"][0]
        except (KeyError, ValueError, IndexError):
            return False
        now = self._clock()
        if keys is None or kid != keys[0] or exp < now or exp > now + MAX_TTL_S:
            return False
        return hmac.compare_digest(sign(keys[1], sha, exp, kid), sig)

    async def _serve(self, w: asyncio.StreamWriter, raw: bytes) -> None:
        if len(raw) > MAX_HEAD:
            await self._reply(w, 431, "Request Header Fields Too Large")
            return
        lines = raw.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in ("HTTP/1.1", "HTTP/1.0"):
            await self._reply(w, 400, "Bad Request")
            return
        method, target = parts[0], parts[1]
        if method not in ("GET", "HEAD"):
            await self._reply(w, 405, "Method Not Allowed", {"Allow": "GET, HEAD"})
            return
        url = urlsplit(target)
        if not url.path.startswith("/m/") or not SHA_RE.match(url.path[3:]):
            await self._reply(w, 404, "Not Found")
            return
        sha = url.path[3:]
        if not self._authorize(sha, parse_qs(url.query)):
            await self._reply(w, 403, "Forbidden")
            return
        path = self.cache.path(sha)
        if path is None or not path.is_file():
            await self._reply(w, 404, "Not Found")
            return
        size = path.stat().st_size
        hdrs = {
            h.partition(":")[0].strip().lower(): h.partition(":")[2].strip()
            for h in lines[1:]
            if ":" in h
        }
        rng = parse_range(hdrs.get("range"), size)
        base = {
            "Accept-Ranges": "bytes",
            "ETag": f'"{sha}"',
            "Content-Type": "application/octet-stream",
        }
        if rng == "invalid":
            await self._reply(w, 416, "Range Not Satisfiable", {"Content-Range": f"bytes */{size}"})
            return
        start, end = rng if isinstance(rng, tuple) else (0, size - 1)
        length = end - start + 1 if size else 0
        status, reason = (206, "Partial Content") if isinstance(rng, tuple) else (200, "OK")
        base["Content-Length"] = str(length)
        if isinstance(rng, tuple):
            base["Content-Range"] = f"bytes {start}-{end}/{size}"
        base |= {
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        head = f"HTTP/1.1 {status} {reason}\r\n"
        head += "".join(f"{k}: {v}\r\n" for k, v in base.items()) + "\r\n"
        w.write(head.encode())
        if method == "GET" and length:
            self.cache.touch(sha)
            with path.open("rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        break
                    w.write(chunk)
                    await w.drain()
                    left -= len(chunk)
        await w.drain()
