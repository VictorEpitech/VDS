"""Client HTTP sortant anti-SSRF (vds-core 1.12.0), fourni aux modules via `ctx.core.safe_http`.

https seulement (port 443), toutes les adresses résolues sont vérifiées puis la connexion est
épinglée sur l'IP vérifiée (SNI et Host d'origine) : pas de rebinding DNS. Redirections
revérifiées une à une, corps coupé à `max_bytes`, délai total borné, ni cookies ni proxy.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import urljoin, urlsplit

import httpx
from vds_sdk.server.http import SafeHttpError, SafeResponse

HARD_MAX_BYTES = 2 * 1024 * 1024
MAX_TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
_FORBIDDEN_HEADERS = frozenset({"host", "cookie", "authorization", "proxy-authorization"})
# 6to4 et NAT64 encapsulent une IPv4 arbitraire : refusés par prudence.
_EMBEDDING_NETS = (ipaddress.ip_network("2002::/16"), ipaddress.ip_network("64:ff9b::/96"))

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def is_public_ip(raw: str) -> bool:
    """Vrai pour une adresse globale routable ; faux pour tout le reste (y compris IPv4-mappée)."""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return False
        if any(ip in net for net in _EMBEDDING_NETS):
            return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _origin_host(public_origin: str | None) -> str | None:
    if not public_origin:
        return None
    try:
        return (urlsplit(public_origin).hostname or "").lower().rstrip(".") or None
    except ValueError:
        return None


class CoreSafeHttp:
    def __init__(
        self,
        *,
        version: str,
        public_origin: str | None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._origin = public_origin
        self._own_host = _origin_host(public_origin)
        self._resolver = resolver
        self._transport = transport
        suffix = f" (+{public_origin})" if public_origin else ""
        self._user_agent = f"VDS/{version}{suffix}"

    async def _check(self, url: str) -> tuple[httpx.URL, str, str]:
        """Valide l'URL ; renvoie (URL épinglée sur l'IP, hôte d'origine, IP)."""
        try:
            parts = urlsplit(url)
            port = parts.port
            host = (parts.hostname or "").lower().rstrip(".")
        except ValueError as exc:
            raise SafeHttpError("net.scheme_refused", "invalid_url") from exc
        if parts.scheme != "https" or not host or parts.username or parts.password:
            raise SafeHttpError("net.scheme_refused")
        if port not in (None, 443):
            raise SafeHttpError("net.scheme_refused", "port")
        if host == self._own_host:
            raise SafeHttpError("net.blocked_address", "own_origin")
        try:
            literal = ipaddress.ip_address(host)
            addresses = [str(literal)]
        except ValueError:
            try:
                addresses = await self._resolver(host, 443)
            except OSError as exc:
                raise SafeHttpError("net.dns_failed", host) from exc
        if not addresses:
            raise SafeHttpError("net.dns_failed", host)
        if not all(is_public_ip(a) for a in addresses):  # toutes, pas seulement la première
            raise SafeHttpError("net.blocked_address", host)
        ip = addresses[0]
        netloc = f"[{ip}]" if ":" in ip else ip
        path = parts.path or "/"
        pinned = httpx.URL(f"https://{netloc}{path}", params=None)
        if parts.query:
            pinned = pinned.copy_with(query=parts.query.encode())
        return pinned, host, ip

    async def get(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_s: float = MAX_TIMEOUT_S,
        headers: Mapping[str, str] | None = None,
        accept: tuple[str, ...] = (),
    ) -> SafeResponse:
        limit = max(1, min(max_bytes, HARD_MAX_BYTES))
        timeout = max(0.1, min(timeout_s, MAX_TIMEOUT_S))
        try:
            async with asyncio.timeout(timeout):
                return await self._get(url, limit, headers or {}, accept, timeout)
        except TimeoutError as exc:
            raise SafeHttpError("net.timeout") from exc

    def _headers(self, extra: Mapping[str, str], accept: tuple[str, ...]) -> dict[str, str]:
        out = {
            k: v for k, v in extra.items() if k.lower() not in _FORBIDDEN_HEADERS | {"user-agent"}
        }
        ua = self._user_agent
        for k, v in extra.items():
            if (
                k.lower() == "user-agent" and v.strip()
            ):  # complément (contact), jamais un remplaçant
                ua = f"{ua} {v.strip()}"[:300]
        out["User-Agent"] = ua
        if accept:
            out["Accept"] = ", ".join(accept)
        out["Accept-Encoding"] = "identity"
        return out

    async def _get(
        self,
        url: str,
        limit: int,
        extra: Mapping[str, str],
        accept: tuple[str, ...],
        timeout: float,  # noqa: ASYNC109 - passé à httpx, le délai global est dans get()
    ) -> SafeResponse:
        headers = self._headers(extra, accept)
        current = url
        async with httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,  # ni proxy ni netrc d'environnement
            timeout=timeout,
        ) as client:
            client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))  # aucun cookie
            for hop in range(MAX_REDIRECTS + 1):
                try:
                    pinned, host, _ip = await self._check(current)
                except SafeHttpError as exc:
                    if hop > 0 and exc.code == "net.scheme_refused":
                        raise SafeHttpError("net.redirect_refused", exc.detail) from exc
                    raise
                req = client.build_request(
                    "GET",
                    pinned,
                    headers={**headers, "Host": host},
                    extensions={"sni_hostname": host},
                )
                try:
                    resp = await client.send(req, stream=True)
                except httpx.TimeoutException as exc:
                    raise SafeHttpError("net.timeout") from exc
                except httpx.HTTPError as exc:
                    raise SafeHttpError("net.unreachable", type(exc).__name__) from exc
                try:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise SafeHttpError("net.redirect_refused", "no_location")
                        current = urljoin(current, location)
                        continue
                    return await self._read(resp, current, limit, accept)
                finally:
                    await resp.aclose()
        raise SafeHttpError("net.redirect_refused", "too_many")

    async def _read(
        self, resp: httpx.Response, final_url: str, limit: int, accept: tuple[str, ...]
    ) -> SafeResponse:
        if not 200 <= resp.status_code < 300:
            raise SafeHttpError("net.http_status", str(resp.status_code))
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise SafeHttpError("net.too_large")
        if accept:
            ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if not any(
                ctype == a.lower() or ctype.endswith("+" + a.split("/")[-1]) for a in accept
            ):
                raise SafeHttpError("net.content_type_refused", ctype)
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise SafeHttpError("net.too_large")
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise SafeHttpError("net.timeout") from exc
        except httpx.HTTPError as exc:
            raise SafeHttpError("net.unreachable", type(exc).__name__) from exc
        return SafeResponse(
            status=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"},
            body=b"".join(chunks),
            final_url=final_url,
        )
