"""notify.mail sans base de données : rendu, envoi SMTP contre un faux serveur, erreurs."""

import importlib.util
import json
import ssl
from collections.abc import Iterator
from datetime import UTC, datetime
from email.header import decode_header, make_header
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from vds_sdk import load_manifest
from vds_sdk.config_schema import secret_keys, validate_config_schema
from vds_sdk.server.notifier import Notification, Recipient

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fakes = _load("notify_mail_fakes", "tests/fakes.py")
mod = _load("notify_mail_module", "server/module.py")


def subject(msg: Any) -> str:
    return str(make_header(decode_header(msg["Subject"])))


class Cfg:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def resolve(self, scope: object = None) -> dict[str, Any]:
        return self.values


class Ctx:
    def __init__(self, values: dict[str, Any]) -> None:
        self.config = Cfg(values)


def config(smtp: Any, **over: Any) -> dict[str, Any]:
    env = smtp.env()
    base: dict[str, Any] = {
        "smtp_host": env["VDS_SMTP_HOST"],
        "smtp_port": int(env["VDS_SMTP_PORT"]),
        "smtp_security": env["VDS_SMTP_SECURITY"],
        "smtp_username": env["VDS_SMTP_USERNAME"],
        "smtp_password": SecretStr(env["VDS_SMTP_PASSWORD"]),
        "from": env["VDS_MAIL_FROM"],
    }
    return base | over


def notifier(values: dict[str, Any]) -> Any:
    n = mod.NotifyMail()
    n._ctx = Ctx(values)
    n._i18n = mod.NotifyMail._load_i18n()
    return n


def notif(**over: Any) -> Notification:
    data: dict[str, Any] = {
        "id": uuid4(),
        "kind": "screen.offline",
        "transition": "opened",
        "severity": "warning",
        "subject": {"screen": "Accueil RDC", "site": "Campus Nord", "timezone": "Europe/Paris"},
        "occurred_at": datetime(2026, 1, 15, 11, 30, tzinfo=UTC),
        "link": "screens/abc-123",
    }
    return Notification(**(data | over))


@pytest.fixture(params=["starttls", "tls"])
def smtp(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    s = fakes.FakeSmtp(mode=request.param).start()
    monkeypatch.setattr(mod, "tls_context", s.trusting_context)
    yield s
    s.stop()


@pytest.fixture(params=["/", "/espace/affichage"])
def base(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("VDS_PUBLIC_ORIGIN", "https://vds.test")
    monkeypatch.setenv("VDS_BASE_PATH", request.param)
    return str(request.param)


async def test_send_fr_renders_names_local_time_and_link(smtp: Any, base: str) -> None:
    n = notifier(config(smtp))
    res = await n.send(notif(), [Recipient(email="admin@example.test", locale="fr")])
    assert res.ok and res.code is None
    msg = smtp.messages[0]
    assert smtp.rcpts[0] == ["admin@example.test"]
    assert subject(msg) == "[VDS] Écran hors ligne : Accueil RDC"
    body = msg.get_payload(decode=True).decode()
    assert "Campus Nord" in body and "15/01/2026 12:30 CET" in body  # heure locale du site
    prefix = "" if base == "/" else base
    assert f"https://vds.test{prefix}/admin/screens/abc-123" in body
    assert "Auto-Submitted" in msg


async def test_send_en_resolved(smtp: Any, base: str) -> None:
    n = notifier(config(smtp))
    res = await n.send(
        notif(
            transition="resolved",
            kind="agent.offline",
            severity="critical",
            subject={"agent": "Pi Moulins", "site": "Campus Nord", "timezone": "Europe/Paris"},
        ),
        [Recipient(email="a@example.test", locale="en")],
    )
    assert res.ok
    msg = smtp.messages[0]
    assert subject(msg) == "[VDS] Resolved - Site agent offline: Pi Moulins"
    assert "2026-01-15 12:30 CET" in msg.get_payload(decode=True).decode()


async def test_no_technical_data_in_mail(smtp: Any, base: str) -> None:
    n = notifier(config(smtp))
    await n.send(notif(), [Recipient(email="a@example.test")])
    raw = smtp.messages[0].as_string()
    assert fakes.PASSWORD not in raw and "127.0.0.1" not in raw and "vds_agt_" not in raw


async def test_header_injection_neutralised(smtp: Any, base: str) -> None:
    n = notifier(config(smtp))
    evil = "Hall\r\nBcc: attaquant@evil.test"
    res = await n.send(
        notif(subject={"screen": evil, "timezone": "Europe/Paris"}),
        [Recipient(email="a@example.test")],
    )
    assert res.ok
    assert smtp.messages[0]["Bcc"] is None and smtp.rcpts[0] == ["a@example.test"]


async def test_certificate_is_verified(smtp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "tls_context", ssl.create_default_context)  # autorité inconnue
    res = await notifier(config(smtp)).send(notif(), [Recipient(email="a@example.test")])
    assert (res.ok, res.code, res.retry_after_s) == (False, "notify.mail.smtp_tls", None)
    assert smtp.messages == []


async def test_bad_password_is_permanent_and_never_leaked(
    smtp: Any, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = config(smtp, smtp_password=SecretStr("mauvais"))
    res = await notifier(cfg).send(notif(), [Recipient(email="a@example.test")])
    assert (res.ok, res.code, res.retry_after_s) == (False, "notify.mail.smtp_auth", None)
    assert "mauvais" not in caplog.text and fakes.PASSWORD not in caplog.text


async def test_temporary_refusal_is_retryable(smtp: Any) -> None:
    smtp.reply = "451 Try again later"
    res = await notifier(config(smtp)).send(notif(), [Recipient(email="a@example.test")])
    assert res.ok is False and res.code == "notify.mail.smtp_rejected" and res.retry_after_s == 60
    smtp.reply = "550 Mailbox unavailable"
    res = await notifier(config(smtp)).send(notif(), [Recipient(email="a@example.test")])
    assert res.code == "notify.mail.smtp_rejected" and res.retry_after_s is None


async def test_connection_refused_is_retryable(smtp: Any) -> None:
    smtp.stop()
    res = await notifier(config(smtp)).send(notif(), [Recipient(email="a@example.test")])
    assert res.code == "notify.mail.connect" and res.retry_after_s == 60


async def test_not_configured_is_degraded_without_sending() -> None:
    n = notifier({"smtp_host": "", "from": ""})
    res = await n.send(notif(), [Recipient(email="a@example.test")])
    assert (res.ok, res.code) == (False, "notify.mail.not_configured")
    health = await n.health()
    assert (health.status, health.code) == ("degraded", "notify.mail.not_configured")


def test_manifest_schema_and_i18n() -> None:
    m = load_manifest(ROOT / "module.json")
    assert m.kind == "notifier" and m.targets == ["server"]
    assert set(m.events.listens) == {"incident.opened", "incident.resolved"}
    schema = json.loads((ROOT / "config.schema.json").read_text("utf-8"))
    validate_config_schema(schema)
    assert secret_keys(schema) == {"smtp_password"}
    for key in ("smtp_host", "smtp_port", "smtp_security", "smtp_username", "from"):
        assert schema["properties"][key]["x-vds-env"].startswith("VDS_")
    assert schema["properties"]["smtp_security"]["enum"] == ["starttls", "tls"]  # pas de mode clair

    def flat(node: Any, prefix: str = "") -> set[str]:
        if isinstance(node, dict):
            return {k for key, v in node.items() for k in flat(v, f"{prefix}{key}.")}
        return {prefix}

    fr = json.loads((ROOT / "i18n" / "fr.json").read_text("utf-8"))
    en = json.loads((ROOT / "i18n" / "en.json").read_text("utf-8"))
    assert flat(fr) == flat(en)
    for prop in schema["properties"].values():
        for k in ("title", "description"):
            assert prop[k].removeprefix("config.") in {x.rstrip(".") for x in flat(fr["config"])}
