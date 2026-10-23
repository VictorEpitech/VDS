"""Appairage des appareils et révocation."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import PairingRequest, Screen, ScreenDevice
from vds_server.player.device_auth import hash_secret, new_token
from vds_server.player.schemas import DeviceInfo

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # Crockford sans I L O U 0 1
CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=15)
POLL_INTERVAL_S = 3
HEARTBEAT_INTERVAL_S = 60
PAIRING_RETENTION = timedelta(hours=1)  # conservation des demandes expirées avant purge
DEVICE_TOKEN_TTL = timedelta(days=90)  # expiration glissante, prolongée par les heartbeats
DEVICE_TOKEN_RENEW_AFTER = timedelta(hours=1)  # au plus une écriture d'expiration par heure


def format_code(code: str) -> str:
    return f"{code[:3]}-{code[3:]}"


def normalize_code(raw: str) -> str:
    code = "".join(ch for ch in raw.upper() if ch.isalnum())
    return code.translate(str.maketrans("OIL", "011"))  # tolérance de saisie Crockford


class PairingService:
    def __init__(self, session: AsyncSession, bus: EventBus | None) -> None:
        self._s = session
        self._bus = bus

    async def _publish(self, events: list[Event]) -> None:
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)

    # --- côté player ------------------------------------------------------
    async def start(self, device: DeviceInfo) -> tuple[PairingRequest, str]:
        secret = secrets.token_urlsafe(24)
        await self._s.execute(
            delete(PairingRequest).where(
                PairingRequest.expires_at < datetime.now(UTC) - PAIRING_RETENTION
            )
        )
        for _ in range(5):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            row = PairingRequest(
                code=code,
                poll_secret_hash=hash_secret(secret),
                device=device.model_dump(),
                expires_at=datetime.now(UTC) + CODE_TTL,
            )
            self._s.add(row)
            try:
                await self._s.commit()
            except IntegrityError:
                await self._s.rollback()
                continue
            return row, secret
        raise ApiError("errors.pairing_code_unavailable", 503)

    async def poll(
        self, pairing_id: UUID, secret: str | None
    ) -> tuple[str, str | None, UUID | None]:
        """Retourne (état, jeton, écran) ; état = pending | done. Lève 401/404/410."""
        row = (
            await self._s.execute(
                select(PairingRequest).where(PairingRequest.id == pairing_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("errors.pairing_not_found", 404)
        if secret is None or not secrets.compare_digest(hash_secret(secret), row.poll_secret_hash):
            raise ApiError("errors.pairing_unauthorized", 401)
        if row.status in ("consumed", "expired"):
            raise ApiError("errors.pairing_gone", 410)
        now = datetime.now(UTC)
        if row.expires_at <= now:
            row.status = "expired"
            await self._s.commit()
            raise ApiError("errors.pairing_gone", 410)
        if row.status == "pending" or row.screen_id is None:
            return "pending", None, None
        return await self._consume(row)

    async def _consume(self, row: PairingRequest) -> tuple[str, str | None, UUID | None]:
        if row.screen_id is None:
            raise ApiError("errors.pairing_gone", 410)
        info: dict[str, Any] = row.device or {}
        token = new_token()
        now = datetime.now(UTC)
        events: list[Event] = []
        old = (
            (
                await self._s.execute(
                    select(ScreenDevice)
                    .where(
                        ScreenDevice.screen_id == row.screen_id, ScreenDevice.revoked_at.is_(None)
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for dev in old:
            dev.revoked_at = now
            dev.revoked_by = row.claimed_by
            audit.record(
                self._s,
                actor_kind="device",
                actor_id=None,
                action="screen.unpaired",
                target_type="screen",
                target_id=str(dev.screen_id),
                details={"device_id": str(dev.id), "reason": "replaced"},
            )
            events.append(_unpaired(dev, "replaced"))
        await self._s.flush()
        device = ScreenDevice(
            screen_id=row.screen_id,
            token_hash=hash_secret(token),
            platform=str(info.get("platform") or "unknown"),
            model=info.get("model"),
            app_version=info.get("app_version"),
            paired_by=row.claimed_by,
            expires_at=now + DEVICE_TOKEN_TTL,
        )
        self._s.add(device)
        await self._s.flush()
        row.status = "consumed"
        audit.record(
            self._s,
            actor_kind="device",
            actor_id=str(device.id),
            action="screen.paired",
            target_type="screen",
            target_id=str(row.screen_id),
            details={"device_id": str(device.id), "platform": device.platform},
        )
        events.append(
            Event(
                name="screen.paired",
                payload={
                    "screen_id": str(row.screen_id),
                    "device_id": str(device.id),
                    "platform": device.platform,
                },
                source="core",
            )
        )
        screen_id = row.screen_id
        await self._s.commit()
        await self._publish(events)
        return "done", token, screen_id

    # --- côté admin -------------------------------------------------------
    async def claim(self, actor: Principal, code: str, screen_id: UUID) -> UUID:
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.archived_at is not None:
            raise ApiError("errors.screen_not_found", 404)
        row = (
            await self._s.execute(
                select(PairingRequest)
                .where(
                    PairingRequest.code == normalize_code(code), PairingRequest.status == "pending"
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("errors.pairing_code_invalid", 404)
        if row.expires_at <= datetime.now(UTC):
            row.status = "expired"
            await self._s.commit()
            raise ApiError("errors.pairing_code_expired", 410)
        row.status = "claimed"
        row.screen_id = screen_id
        row.claimed_by = actor.user_id
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="pairing.claimed",
            target_type="screen",
            target_id=str(screen_id),
            details={"pairing_id": str(row.id)},
        )
        await self._s.commit()
        return screen_id

    async def revoke(self, actor: Principal, screen_id: UUID) -> None:
        dev = (
            await self._s.execute(
                select(ScreenDevice)
                .where(ScreenDevice.screen_id == screen_id, ScreenDevice.revoked_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if dev is None:
            raise ApiError("errors.device_not_found", 404)
        dev.revoked_at = datetime.now(UTC)
        dev.revoked_by = actor.user_id
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="screen.unpaired",
            target_type="screen",
            target_id=str(screen_id),
            details={"device_id": str(dev.id), "reason": "revoked"},
        )
        event = _unpaired(dev, "revoked")
        await self._s.commit()
        await self._publish([event])
        # une demande déjà réclamée mais non consommée pour cet écran n'est plus valable
        await self._s.execute(
            update(PairingRequest)
            .where(PairingRequest.screen_id == screen_id, PairingRequest.status == "claimed")
            .values(status="expired")
        )
        await self._s.commit()


def _unpaired(dev: ScreenDevice, reason: str) -> Event:
    return Event(
        name="screen.unpaired",
        payload={"screen_id": str(dev.screen_id), "device_id": str(dev.id), "reason": reason},
        source="core",
    )


async def heartbeat(
    session: AsyncSession, device_id: UUID, body: dict[str, Any], app_version: str | None
) -> None:
    now = datetime.now(UTC)
    values: dict[str, Any] = {"last_seen_at": now, "last_heartbeat": body}
    if app_version:
        values["app_version"] = app_version
    await session.execute(update(ScreenDevice).where(ScreenDevice.id == device_id).values(**values))
    # expiration glissante : un appareil vivant n'expire pas, un appareil abandonné si
    await session.execute(
        update(ScreenDevice)
        .where(
            ScreenDevice.id == device_id,
            ScreenDevice.expires_at < now + DEVICE_TOKEN_TTL - DEVICE_TOKEN_RENEW_AFTER,
        )
        .values(expires_at=now + DEVICE_TOKEN_TTL)
    )
    await session.commit()
