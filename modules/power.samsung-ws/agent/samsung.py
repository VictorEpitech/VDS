"""Client Samsung Tizen : REST 8001 (état) et WebSocket TLS 8002 (touches, jeton, empreinte TLS)."""

import asyncio
import base64
import hashlib
import json
import ssl
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException


class SamsungError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class KeySession:
    token: str | None
    fingerprint: str


class SamsungTransport(Protocol):
    async def device_info(self, host: str, timeout: float) -> dict[str, Any] | None: ...

    async def send_key(
        self,
        host: str,
        port: int,
        name: str,
        token: str | None,
        pin: str | None,
        key: str | None,
        timeout: float,
    ) -> KeySession: ...


def _tv_ssl() -> ssl.SSLContext:
    # La TV présente un certificat auto-signé : l'authenticité est assurée par l'épinglage
    # de l'empreinte (TOFU) vérifié AVANT d'envoyer le jeton, pas par une AC.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _Pinned(ClientConnection):
    """Connexion WebSocket qui vérifie l'empreinte TLS sur SA propre session, avant toute requête.

    `connection_made` s'exécute après la poignée de main TLS et avant l'envoi de la requête
    d'upgrade (où voyage le jeton) : en cas d'écart, le transport est coupé, rien n'est émis.
    """

    pin: str | None = None
    peer_fp: str | None = None
    pin_failed: bool = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)  # type: ignore[arg-type]
        obj = transport.get_extra_info("ssl_object")
        der = obj.getpeercert(binary_form=True) if obj is not None else None
        self.peer_fp = hashlib.sha256(der).hexdigest() if der else None
        if self.peer_fp is None or (self.pin is not None and self.peer_fp != self.pin):
            self.pin_failed = True
            transport.abort()


class WsSamsungTransport:
    async def device_info(self, host: str, timeout: float) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(f"http://{host}:8001/api/v2/")  # noqa: S113
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def send_key(
        self,
        host: str,
        port: int,
        name: str,
        token: str | None,
        pin: str | None,
        key: str | None,
        timeout: float,
    ) -> KeySession:
        try:
            return await asyncio.wait_for(
                self._send(host, port, name, token, pin, key, timeout), timeout + 2
            )
        except TimeoutError as e:
            raise SamsungError(
                "power.samsung.not_paired" if token is None else "power.samsung.timeout"
            ) from e
        except (OSError, WebSocketException) as e:
            raise SamsungError("power.samsung.unreachable") from e

    async def _send(
        self,
        host: str,
        port: int,
        name: str,
        token: str | None,
        pin: str | None,
        key: str | None,
        timeout: float,
    ) -> KeySession:
        conns: list[_Pinned] = []

        class Conn(_Pinned):
            def __init__(self, *a: Any, **kw: Any) -> None:
                super().__init__(*a, **kw)
                self.pin = pin
                conns.append(self)

        b64 = base64.b64encode(name.encode()).decode()
        url = f"wss://{host}:{port}/api/v2/channels/samsung.remote.control?name={quote(b64)}"
        if token:
            url += f"&token={quote(token)}"
        try:
            ws = await connect(
                url,
                ssl=_tv_ssl(),
                open_timeout=timeout,
                max_size=1 << 20,
                proxy=None,  # le jeton ne transite jamais par un proxy
                create_connection=Conn,
            )
        except (OSError, WebSocketException):
            if conns and conns[0].pin_failed:
                raise SamsungError("power.samsung.cert_changed") from None
            raise
        fp = conns[0].peer_fp or ""
        async with ws:
            new_token = token
            while True:
                msg = json.loads(await ws.recv())
                ev = msg.get("event")
                if ev == "ms.channel.connect":
                    new_token = (msg.get("data") or {}).get("token") or token
                    break
                if ev in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                    raise SamsungError("power.samsung.not_paired")
                if ev == "ms.error":
                    raise SamsungError("power.samsung.error")
            if key is not None:
                await ws.send(
                    json.dumps(
                        {
                            "method": "ms.remote.control",
                            "params": {
                                "Cmd": "Click",
                                "DataOfCmd": key,
                                "Option": "false",
                                "TypeOfRemote": "SendRemoteKey",
                            },
                        }
                    )
                )
                await asyncio.sleep(0.3)  # laisse la TV traiter avant la fermeture
        return KeySession(new_token, fp)
