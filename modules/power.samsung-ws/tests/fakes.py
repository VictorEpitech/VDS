"""Faux TV Samsung : REST 8001 (HTTP) et WebSocket TLS ms.remote.control avec flux de jeton."""

import asyncio
import base64
import datetime
import json
import ssl
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets.asyncio.server import serve


def self_signed(dirpath: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tv")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    c, k = dirpath / "c.pem", dirpath / "k.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(c), str(k)


class FakeSamsungTv:
    """mode: 'accept' | 'deny' | 'ignore' (aucune réponse : l'utilisateur n'accepte pas)."""

    def __init__(self, mode: str = "accept", power: str = "on") -> None:
        self.mode = mode
        self.power = power
        self.token = "13579246"
        self.keys: list[str] = []
        self.names: list[str] = []
        self.tokens_seen: list[str | None] = []
        self.ws_port = 0
        self.rest_port = 0
        self.rest_enabled = True
        self._tmp = tempfile.TemporaryDirectory()
        self._ws = None
        self._rest: asyncio.Server | None = None

    async def __aenter__(self) -> "FakeSamsungTv":
        cert, key = self_signed(Path(self._tmp.name))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self._ws = await serve(self._handle, "127.0.0.1", 0, ssl=ctx)
        self.ws_port = self._ws.sockets[0].getsockname()[1]
        self._rest = await asyncio.start_server(self._http, "127.0.0.1", 0)
        self.rest_port = self._rest.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *a: object) -> None:
        assert self._ws and self._rest
        self._ws.close()
        self._rest.close()
        await self._ws.wait_closed()
        self._tmp.cleanup()

    async def _http(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await r.readuntil(b"\r\n\r\n")
        if self.rest_enabled:
            body = json.dumps(
                {
                    "name": "[TV] Samsung 55",
                    "device": {
                        "type": "Samsung SmartTV",
                        "modelName": "QE55QN90B",
                        "PowerState": self.power,
                        "wifiMac": "AA:BB:CC:00:11:22",
                    },
                }
            ).encode()
            w.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
        else:
            w.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
        await w.drain()
        w.close()

    async def _handle(self, ws) -> None:  # type: ignore[no-untyped-def]
        q = parse_qs(urlsplit(ws.request.path).query)
        self.names.append(base64.b64decode(q["name"][0]).decode())
        tok = q.get("token", [None])[0]
        self.tokens_seen.append(tok)
        if tok == self.token:
            pass
        elif self.mode == "ignore":
            await asyncio.sleep(2)
            return
        elif self.mode == "deny" or tok is not None:
            await ws.send(json.dumps({"event": "ms.channel.unauthorized"}))
            return
        data = {} if tok == self.token else {"token": self.token}
        await ws.send(json.dumps({"event": "ms.channel.connect", "data": data}))
        await ws.send(json.dumps({"event": "ms.channel.ready"}))
        async for raw in ws:
            msg = json.loads(raw)
            key = msg["params"]["DataOfCmd"]
            self.keys.append(key)
            if key == "KEY_POWER":
                self.power = "standby" if self.power == "on" else "on"
