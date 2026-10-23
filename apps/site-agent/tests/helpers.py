"""Faux serveur /ws/agent (TLS, AC privée de test) et module pilote factice."""

import asyncio
import datetime
import ipaddress
import json
import ssl
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from vds_sdk.wire import Frame, decode_frame, encode_frame
from websockets.asyncio.server import ServerConnection, serve


def make_cert(d: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vds-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    c, k = d / "ca.pem", d / "key.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(c), str(k)


def welcome_data(
    modules: list[str],
    resume_from: int = 0,
    plan_revision: str | None = None,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "cache": cache or {},
        "agent_id": str(uuid4()),
        "site_id": str(uuid4()),
        "session_id": str(uuid4()),
        "server_time": datetime.datetime.now(datetime.UTC).isoformat(),
        "resume_from": resume_from,
        "plan_revision": plan_revision,
        "modules_enabled": [{"id": m, "version": "0.1.0"} for m in modules],
    }


class FakeServer:
    def __init__(self, tmp: Path, modules: list[str], plan_revision: str | None = None) -> None:
        self.ca, key = make_cert(tmp)
        self.key = key
        self.modules = modules
        self.plan_revision = plan_revision
        self.cache: dict[str, Any] | None = None
        self.received: list[Frame] = []
        self.headers: list[dict[str, str]] = []
        self.subprotocols: list[str | None] = []
        self.hellos: list[dict[str, Any]] = []
        self.to_send: list[Frame] = []
        self.resume_from = 0
        self.close_after_welcome: int | None = None
        self.connections = 0
        self.url = ""
        self._srv = None

    async def __aenter__(self) -> "FakeServer":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.ca, self.key)
        self._srv = await serve(self._h, "127.0.0.1", 0, ssl=ctx, subprotocols=["vds.agent.v1"])  # type: ignore[list-item]
        port = self._srv.sockets[0].getsockname()[1]
        self.url = f"https://127.0.0.1:{port}/espace/affichage"
        return self

    async def __aexit__(self, *a: object) -> None:
        assert self._srv
        self._srv.close()
        await self._srv.wait_closed()

    async def _h(self, ws: ServerConnection) -> None:
        self.connections += 1
        self.headers.append(dict(ws.request.headers.items()))
        self.subprotocols.append(ws.subprotocol)
        self.path = ws.request.path
        hello = decode_frame(await ws.recv())
        self.hellos.append(hello.data)
        await ws.send(
            encode_frame(
                Frame(
                    type="welcome",
                    data=welcome_data(
                        self.modules, self.resume_from, self.plan_revision, self.cache
                    ),
                )
            )
        )
        if self.close_after_welcome:
            await ws.close(self.close_after_welcome)
            return
        for f in self.to_send:
            await ws.send(encode_frame(f))
        try:
            async for raw in ws:
                self.received.append(decode_frame(raw))
        except Exception:  # noqa: BLE001
            pass

    def results(self) -> list[dict[str, Any]]:
        return [f.data for f in self.received if f.type == "cmd.result"]

    def events(self) -> list[dict[str, Any]]:
        return [f.data for f in self.received if f.type == "event"]


def cmd_frame(
    seq: int, name: str, args: dict[str, Any], screen_id: str | None = None, expires_in_s: int = 300
) -> Frame:
    exp = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=expires_in_s)
    data: dict[str, Any] = {
        "cmd_id": str(uuid4()),
        "name": name,
        "args": args,
        "expires_at": exp.isoformat(),
    }
    if screen_id:
        data["screen_id"] = screen_id
    return Frame(type="cmd", seq=seq, data=data)


FAKE_MODULE = """
from typing import ClassVar
from vds_sdk.agent import PowerDriver, PowerResult

STATE = {"s": "off", "fail": False, "calls": []}

class FakeDriver(PowerDriver):
    capabilities: ClassVar[frozenset] = frozenset({"power.on", "power.off", "power.state"})
    async def set_power(self, target, state):
        STATE["calls"].append(state)
        if not STATE["fail"]:
            STATE["s"] = state
        return PowerResult(ok=True, state="unknown", verified=False)
    async def get_state(self, target):
        return STATE["s"]
"""


def write_fake_module(modules_dir: Path) -> Path:
    d = modules_dir / "power.fake"
    (d / "agent").mkdir(parents=True)
    (d / "module.json").write_text(
        json.dumps(
            {
                "id": "power.fake",
                "version": "0.1.0",
                "core": ">=1.2 <2.0",
                "kind": "power-driver",
                "name": {"fr": "Faux", "en": "Fake"},
                "description": {"fr": "Faux", "en": "Fake"},
                "targets": ["site-agent"],
                "entrypoints": {"site-agent": "agent.module:FakeDriver"},
                "capabilities": ["power.on", "power.off", "power.state"],
                "permissions": [],
                "events": {"listens": [], "emits": []},
                "author": "test",
            }
        )
    )
    (d / "agent" / "module.py").write_text(FAKE_MODULE)
    return d


async def eventually(pred: Any, timeout: float = 5.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.02)
