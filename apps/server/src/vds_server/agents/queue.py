"""File `remote_command` : seq par cible, signalisation NOTIFY, résultats idempotents."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vds_server.db.models_power import RemoteCommand

CHANNEL = "vds_command"
DEFAULT_TTL_S = 300
DEVICE_KIND = "device"  # connexion /ws/player d'un appareil (la plus récente gagne)
MODULES_KIND = "modules"  # signal à tous les workers : l'état des modules a changé


def notify_payload(kind: str, target_id: UUID) -> str:
    return f"{kind}:{target_id}"


async def notify_raw(session: AsyncSession, kind: str, value: str) -> None:
    await session.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": f"{kind}:{value}"})


async def notify(session: AsyncSession, kind: str, target_id: UUID) -> None:
    """NOTIFY transactionnel : émis au commit de la transaction courante."""
    await session.execute(
        text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": notify_payload(kind, target_id)}
    )


@dataclass(frozen=True)
class QueuedCommand:
    id: UUID
    seq: int
    name: str
    screen_id: UUID | None
    args: dict[str, Any]
    correlation_id: UUID | None
    expires_at: datetime


def _view(row: RemoteCommand) -> QueuedCommand:
    return QueuedCommand(
        row.id, row.seq, row.name, row.screen_id, dict(row.args or {}), row.correlation_id,
        row.expires_at,
    )  # fmt: skip


class CommandQueue:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def enqueue(
        self,
        kind: str,
        target_id: UUID,
        name: str,
        *,
        screen_id: UUID | None = None,
        args: dict[str, Any] | None = None,
        ttl_s: int = DEFAULT_TTL_S,
        created_by: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> RemoteCommand:
        async with self._maker.begin() as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"rc:{kind}:{target_id}"},
            )
            seq = (
                await s.execute(
                    select(func.coalesce(func.max(RemoteCommand.seq), 0)).where(
                        RemoteCommand.target_kind == kind, RemoteCommand.target_id == target_id
                    )
                )
            ).scalar_one()
            row = RemoteCommand(
                target_kind=kind,
                target_id=target_id,
                seq=seq + 1,
                name=name,
                screen_id=screen_id,
                args=args or {},
                status="queued",
                correlation_id=correlation_id,
                created_by=created_by,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            )
            s.add(row)
            await s.flush()
            await notify(s, kind, target_id)
        return row

    async def pending(
        self, kind: str, target_id: UUID, after_seq: int, *, limit: int = 100
    ) -> list[QueuedCommand]:
        """Commandes `queued|sent` non expirées, seq > after_seq (périmées -> `expired`)."""
        now = datetime.now(UTC)
        async with self._maker.begin() as s:
            await s.execute(
                update(RemoteCommand)
                .where(
                    RemoteCommand.target_kind == kind,
                    RemoteCommand.target_id == target_id,
                    RemoteCommand.status.in_(("queued", "sent")),
                    RemoteCommand.expires_at <= now,
                )
                .values(status="expired", code="command.expired", done_at=now)
            )
            rows = (
                await s.execute(
                    select(RemoteCommand)
                    .where(
                        RemoteCommand.target_kind == kind,
                        RemoteCommand.target_id == target_id,
                        RemoteCommand.status.in_(("queued", "sent")),
                        RemoteCommand.seq > after_seq,
                    )
                    .order_by(RemoteCommand.seq)
                    .limit(limit)
                )
            ).scalars()
            return [_view(r) for r in rows]

    async def mark_sent(self, ids: list[UUID]) -> None:
        if not ids:
            return
        async with self._maker.begin() as s:
            await s.execute(
                update(RemoteCommand)
                .where(RemoteCommand.id.in_(ids), RemoteCommand.status == "queued")
                .values(status="sent", sent_at=datetime.now(UTC))
            )

    async def complete(
        self,
        kind: str,
        target_id: UUID,
        cmd_id: UUID,
        status: str,
        code: str | None,
        data: dict[str, Any],
    ) -> bool:
        """Enregistre un résultat ; idempotent (un résultat déjà posé n'est pas écrasé)."""
        async with self._maker.begin() as s:
            res = await s.execute(
                update(RemoteCommand)
                .where(
                    RemoteCommand.id == cmd_id,
                    RemoteCommand.target_kind == kind,
                    RemoteCommand.target_id == target_id,
                    RemoteCommand.status.in_(("queued", "sent")),
                )
                .values(status=status, code=code, result=data, done_at=datetime.now(UTC))
            )
            return bool(getattr(res, "rowcount", 0))

    async def recent(self, kind: str, target_id: UUID, limit: int = 50) -> list[RemoteCommand]:
        async with self._maker() as s:
            return list(
                (
                    await s.execute(
                        select(RemoteCommand)
                        .where(
                            RemoteCommand.target_kind == kind, RemoteCommand.target_id == target_id
                        )
                        .order_by(RemoteCommand.seq.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
