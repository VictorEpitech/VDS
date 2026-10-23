"""notify.mail : e-mail à l'ouverture/résolution des incidents (SMTP, TLS toujours vérifié).

Connexion SMTP uniquement par l'environnement (`x-vds-env`), mot de passe `x-vds-secret` :
jamais journalisé, jamais écrit en base. Ne dépend que de `vds_sdk` et de `ctx.core`.
"""

import asyncio
import contextlib
import logging
import os
import re
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import aiosmtplib
from sqlalchemy import text
from vds_sdk import Event, Health, ModuleContext
from vds_sdk.server.notifier import DeliveryResult, Notification, Notifier, Recipient

I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"
LOCALES = ("fr", "en")
RETRY_DELAYS_S = (60, 300, 900)  # 1, 5 et 15 min après le 1er, 2e et 3e échec transitoire
CLAIM_S = 120  # une tentative en cours n'est pas reprise avant ce délai
PERIOD_S = 30.0
SMTP_TIMEOUT_S = 20.0
NOT_CONFIGURED = "notify.mail.not_configured"
_NS = UUID("6f0c3a52-3c1a-4a0e-9d57-2b1c8a6f4e11")
_LINK_RE = re.compile(r"^[A-Za-z0-9/_.\-]{1,200}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def tls_context() -> ssl.SSLContext:
    """Contexte TLS : certificat et nom d'hôte toujours vérifiés (surchargé uniquement en test
    pour faire confiance à une autorité de test, jamais pour désactiver la vérification)."""
    return ssl.create_default_context()


def _clean(value: str, limit: int = 200) -> str:
    return _CTRL_RE.sub(" ", value).strip()[:limit]


def notification_id(incident_id: UUID, transition: str) -> UUID:
    return uuid5(_NS, f"{incident_id}:{transition}")


def _chain(exc: BaseException | None) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and exc not in out:
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def classify(exc: BaseException) -> DeliveryResult:
    """Erreur SMTP -> code stable ; `retry_after_s` seulement pour les erreurs transitoires."""
    chain = _chain(exc)
    if any(isinstance(e, ssl.SSLError | ssl.CertificateError) for e in chain):
        return DeliveryResult(ok=False, code="notify.mail.smtp_tls")
    if any(isinstance(e, aiosmtplib.SMTPAuthenticationError) for e in chain):
        return DeliveryResult(ok=False, code="notify.mail.smtp_auth")
    if any(isinstance(e, aiosmtplib.SMTPTimeoutError | TimeoutError) for e in chain):
        return DeliveryResult(ok=False, code="notify.mail.timeout", retry_after_s=RETRY_DELAYS_S[0])
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        if exc.code >= 500:
            return DeliveryResult(ok=False, code="notify.mail.smtp_rejected")
        return DeliveryResult(
            ok=False, code="notify.mail.smtp_rejected", retry_after_s=RETRY_DELAYS_S[0]
        )
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        return DeliveryResult(ok=False, code="notify.mail.smtp_rejected")
    return DeliveryResult(ok=False, code="notify.mail.connect", retry_after_s=RETRY_DELAYS_S[0])


def _is_configured(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("smtp_host")) and bool(cfg.get("from"))


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if getter else (value or ""))


class NotifyMail(Notifier):
    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._i18n: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger("vds.module.notify.mail")

    # --- cycle de vie -----------------------------------------------------------
    async def setup(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        self._i18n = await asyncio.to_thread(self._load_i18n)
        ctx.bus.subscribe("incident.opened", self._on_event)
        ctx.bus.subscribe("incident.resolved", self._on_event)
        self._task = asyncio.create_task(self._retry_loop())

    async def teardown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def health(self) -> Health:
        if self._ctx is None or not _is_configured(await self._ctx.config.resolve()):
            return Health(status="degraded", code=NOT_CONFIGURED)
        return Health()

    @staticmethod
    def _load_i18n() -> dict[str, dict[str, Any]]:
        import json

        return {
            loc: json.loads((I18N_DIR / f"{loc}.json").read_text("utf-8")).get("mail", {})
            for loc in LOCALES
        }

    def _t(self, locale: str, key: str, **params: str) -> str:
        for loc in (locale if locale in LOCALES else "fr", "fr"):
            node: Any = self._i18n.get(loc, {})
            for part in key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, str):
                return node.format(**params)
        return key

    @property
    def _core(self) -> Any:
        assert self._ctx is not None  # noqa: S101 - setup() précède tout usage
        return self._ctx.core

    # --- réception des incidents --------------------------------------------------
    async def _on_event(self, event: Event) -> None:
        assert self._ctx is not None  # noqa: S101
        cfg = await self._ctx.config.resolve()
        if not _is_configured(cfg):
            self._log.warning("notify.mail.not_configured")
            return
        transition = "opened" if event.name == "incident.opened" else "resolved"
        if transition == "resolved" and not cfg.get("send_resolved", True):
            return
        if event.payload.get("kind") not in cfg.get("kinds", []):
            return
        incident_id = UUID(str(event.payload["incident_id"]))
        recipients = await self._recipients(cfg)
        if not recipients:
            return
        nid = notification_id(incident_id, transition)
        ids: list[UUID] = []
        async with self._core.db.session() as s:
            for r in recipients:
                row = await s.execute(
                    text(
                        "INSERT INTO m_notify_mail_delivery"
                        " (notification_id, incident_id, transition, recipient, locale)"
                        " VALUES (:n, :i, :t, :r, :l)"
                        " ON CONFLICT (notification_id, recipient) DO NOTHING RETURNING id"
                    ),
                    {"n": nid, "i": incident_id, "t": transition, "r": r.email, "l": r.locale},
                )
                if (new_id := row.scalar_one_or_none()) is not None:
                    ids.append(new_id)
            await s.commit()
        for delivery_id in ids:
            await self._attempt(delivery_id)

    async def _recipients(self, cfg: dict[str, Any]) -> list[Recipient]:
        found: dict[str, Recipient] = {}
        if cfg.get("notify_admins", True):
            for u in await self._core.users.list(role="admin"):
                locale = u["locale"] if u["locale"] in LOCALES else "fr"
                found.setdefault(u["email"].lower(), Recipient(email=u["email"], locale=locale))
        for email in cfg.get("extra_recipients", []):
            found.setdefault(email.lower(), Recipient(email=email))
        return list(found.values())

    # --- envoi idempotent avec reprises ---------------------------------------------
    async def _attempt(self, delivery_id: UUID) -> None:
        async with self._core.db.session() as s:
            claimed = (
                await s.execute(
                    text(
                        "UPDATE m_notify_mail_delivery SET next_attempt_at ="
                        " now() + make_interval(secs => :claim)"
                        " WHERE id = :id AND status = 'pending' AND next_attempt_at <= now()"
                        " RETURNING incident_id, transition, recipient, locale, attempts"
                    ),
                    {"id": delivery_id, "claim": CLAIM_S},
                )
            ).first()
            await s.commit()
        if claimed is None:
            return
        incident_id, transition, email, locale, attempts = claimed
        view = await self._core.incidents.get(incident_id)
        if view is None:
            result = DeliveryResult(ok=False, code="notify.mail.incident_gone")
        else:
            n = self._notification(view, transition)
            result = await self.send(n, [Recipient(email=email, locale=locale)])
        await self._record(delivery_id, result, attempts + 1)

    @staticmethod
    def _notification(view: dict[str, Any], transition: str) -> Notification:
        at = view["resolved_at"] if transition == "resolved" else view["opened_at"]
        subject = {k: str(v) for k, v in view["subject"].items()}
        subject["timezone"] = str(view["timezone"])
        link = {"screen": "screens/", "agent": "sites/"}.get(view["subject_type"])
        target = view["subject_id"] if view["subject_type"] == "screen" else view["site_id"]
        return Notification(
            id=notification_id(view["id"], transition),
            kind=view["kind"],
            transition=transition,
            severity=view["severity"],
            subject=subject,
            occurred_at=at or datetime.now(UTC),
            link=f"{link}{target}" if link and target else "incidents",
        )

    async def _record(self, delivery_id: UUID, result: DeliveryResult, attempts: int) -> None:
        if result.ok:
            sql = (
                "UPDATE m_notify_mail_delivery SET status='sent', code=NULL, attempts=:a,"
                " sent_at=now() WHERE id=:id"
            )
            params: dict[str, Any] = {"id": delivery_id, "a": attempts}
        elif result.retry_after_s is None or attempts > len(RETRY_DELAYS_S):
            sql = (
                "UPDATE m_notify_mail_delivery SET status='failed', code=:c, attempts=:a"
                " WHERE id=:id"
            )
            params = {"id": delivery_id, "a": attempts, "c": result.code}
        else:
            sql = (
                "UPDATE m_notify_mail_delivery SET code=:c, attempts=:a,"
                " next_attempt_at = now() + make_interval(secs => :d) WHERE id=:id"
            )
            params = {
                "id": delivery_id,
                "a": attempts,
                "c": result.code,
                "d": RETRY_DELAYS_S[attempts - 1],
            }
        async with self._core.db.session() as s:
            await s.execute(text(sql), params)
            await s.commit()
        if not result.ok:
            self._log.warning("notify.mail.delivery_failed", extra={"code": result.code})

    async def _retry_loop(self) -> None:
        while True:
            await asyncio.sleep(PERIOD_S)
            with contextlib.suppress(Exception):
                await self.retry_due()

    async def retry_due(self) -> int:
        async with self._core.db.session() as s:
            ids = list(
                (
                    await s.execute(
                        text(
                            "SELECT id FROM m_notify_mail_delivery WHERE status='pending'"
                            " AND next_attempt_at <= now() ORDER BY next_attempt_at LIMIT 50"
                        )
                    )
                ).scalars()
            )
        for delivery_id in ids:
            await self._attempt(delivery_id)
        return len(ids)

    # --- interface Notifier -----------------------------------------------------------
    async def send(self, n: Notification, recipients: list[Recipient]) -> DeliveryResult:
        assert self._ctx is not None  # noqa: S101
        cfg = await self._ctx.config.resolve()
        if not _is_configured(cfg):
            return DeliveryResult(ok=False, code=NOT_CONFIGURED)
        security = cfg.get("smtp_security", "starttls")
        if security not in ("starttls", "tls"):
            return DeliveryResult(ok=False, code=NOT_CONFIGURED)
        username = str(cfg.get("smtp_username") or "")
        for r in recipients:
            try:
                await aiosmtplib.send(
                    self.render(n, r, str(cfg["from"])),
                    hostname=str(cfg["smtp_host"]),
                    port=int(cfg.get("smtp_port", 587)),
                    username=username or None,
                    password=_secret(cfg.get("smtp_password")) if username else None,
                    use_tls=security == "tls",
                    start_tls=security == "starttls",
                    tls_context=tls_context(),
                    timeout=SMTP_TIMEOUT_S,
                )
            except Exception as exc:
                return classify(exc)
        return DeliveryResult(ok=True)

    # --- rendu ---------------------------------------------------------------------------
    def render(self, n: Notification, recipient: Recipient, sender: str) -> EmailMessage:
        loc = recipient.locale if recipient.locale in LOCALES else "fr"
        names = {k: _clean(v) for k, v in n.subject.items()}
        tz_name = names.pop("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        local = n.occurred_at.astimezone(tz)
        fmt = "%d/%m/%Y %H:%M %Z" if loc == "fr" else "%Y-%m-%d %H:%M %Z"
        kind_key = re.sub(r"\W", "_", n.kind)
        title = self._t(loc, f"kind.{kind_key}")
        if title == f"kind.{kind_key}":
            title = self._t(loc, "kind.unknown")
        main = next((names[k] for k in ("screen", "agent", "module", "site") if k in names), "")
        lines = [self._t(loc, f"intro.{n.transition}"), "", title]
        for field in ("screen", "agent", "module", "site"):
            if field in names:
                lines.append(f"{self._t(loc, f'field.{field}')} : {names[field]}")
        lines.append(f"{self._t(loc, 'field.severity')} : {self._t(loc, f'severity.{n.severity}')}")
        lines.append(f"{self._t(loc, 'field.time')} : {local.strftime(fmt)}")
        link = self._link(n.link)
        if link:
            lines += ["", self._t(loc, "open_link", link=link)]
        msg = EmailMessage()
        msg["From"] = _clean(sender, 254)
        msg["To"] = _clean(recipient.email, 254)
        msg["Subject"] = self._t(loc, f"subject.{n.transition}", title=title, name=main)
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="vds.invalid")
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content("\n".join(lines) + "\n", charset="utf-8")
        return msg

    @staticmethod
    def _link(path: str) -> str | None:
        origin = (os.environ.get("VDS_PUBLIC_ORIGIN") or "").rstrip("/")
        if not origin or not _LINK_RE.match(path):
            return None
        base = (os.environ.get("VDS_BASE_PATH") or "/").strip()
        prefix = "" if base in ("", "/") else base.rstrip("/")
        return f"{origin}{prefix}/admin/{path.lstrip('/')}"
