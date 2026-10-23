"""Faux serveur SMTP (aiosmtpd) avec STARTTLS ou TLS implicite et certificat de test éphémère."""

import datetime as dt
import ipaddress
import socket
import ssl
import tempfile
import warnings
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from pathlib import Path
from typing import Any

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

USER = "vds-mailer"
PASSWORD = "pw-" + "test-only-9f3a"  # noqa: S105 - valeur de test, jamais un secret réel


def make_cert(directory: Path) -> tuple[Path, Path, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    crt = directory / "cert.pem"
    k = directory / "key.pem"
    pem = cert.public_bytes(serialization.Encoding.PEM)
    crt.write_bytes(pem)
    k.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return crt, k, pem.decode()


class _Handler:
    def __init__(self, fake: "FakeSmtp") -> None:
        self.fake = fake

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
        if self.fake.reply:
            return self.fake.reply
        self.fake.messages.append(message_from_bytes(envelope.content))
        self.fake.rcpts.append(list(envelope.rcpt_tos))
        return "250 OK"


def _auth(server: Any, session: Any, envelope: Any, mechanism: str, data: Any) -> AuthResult:
    ok = (
        isinstance(data, LoginPassword)
        and data.login == USER.encode()
        and data.password == PASSWORD.encode()
    )
    return AuthResult(success=ok, handled=False)


@dataclass
class FakeSmtp:
    mode: str = "starttls"  # "starttls" | "tls"
    messages: list[Message] = field(default_factory=list)
    rcpts: list[list[str]] = field(default_factory=list)
    reply: str | None = None  # ex. "451 try later" : refuse le DATA
    cert_pem: str = ""
    port: int = 0
    _tmp: tempfile.TemporaryDirectory[str] | None = None
    _ctl: Controller | None = None

    def start(self) -> "FakeSmtp":
        self._tmp = tempfile.TemporaryDirectory()
        crt, key, self.cert_pem = make_cert(Path(self._tmp.name))
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(crt, key)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        kw: dict[str, Any] = (
            {"ssl_context": server_ctx, "auth_require_tls": False}  # TLS déjà établi
            if self.mode == "tls"
            else {"tls_context": server_ctx, "require_starttls": True, "auth_require_tls": True}
        )
        warnings.filterwarnings("ignore", message="Requiring AUTH while not requiring TLS")
        self._ctl = Controller(
            _Handler(self),
            hostname="127.0.0.1",
            port=self.port,
            authenticator=_auth,
            auth_required=True,
            **kw,
        )
        self._ctl.start()
        return self

    def stop(self) -> None:
        if self._ctl is not None:
            self._ctl.stop(no_assert=True)  # idempotent
            self._ctl = None
        if self._tmp is not None:
            self._tmp.cleanup()

    def trusting_context(self) -> ssl.SSLContext:
        """Contexte client qui vérifie le certificat contre l'autorité de test."""
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cadata=self.cert_pem)
        return ctx

    def env(self) -> dict[str, str]:
        return {
            "VDS_SMTP_HOST": "localhost",
            "VDS_SMTP_PORT": str(self.port),
            "VDS_SMTP_SECURITY": self.mode,
            "VDS_SMTP_USERNAME": USER,
            "VDS_SMTP_PASSWORD": PASSWORD,
            "VDS_MAIL_FROM": "vds@example.test",
        }
