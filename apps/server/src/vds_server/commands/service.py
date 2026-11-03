"""Commandes player (lot I) : création, livraison, résultats, annulation, expiration.

Table dédiée `player_command` (la file d'agent `remote_command` est append-only à `seq`). Toute
écriture auditée l'est dans la transaction ; les événements sont publiés après le commit.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus
from vds_sdk.wire import PLAYER_COMMAND_ARGS, CmdResult

from vds_server.agents.queue import SCREEN_KIND, notify_raw
from vds_server.commands import schemas as sc
from vds_server.core import audit
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen, ScreenDevice, UserAccount
from vds_server.db.models_command import PlayerCommand

PENDING = ("queued", "sent")
FAILURES = ("failed", "expired", "rejected")
TTL_S: dict[str, tuple[int, int]] = {  # nom -> (défaut, max)
    "reload": (600, 86400),
    "restart": (600, 86400),
    "identify": (120, 600),
    "screenshot": (120, 600),
    "cache.resync": (3600, 86400),
}
ADMIN_ONLY = frozenset({"restart", "cache.resync"})
SERVER_MARK = "_server"  # posé uniquement par l'ingestion serveur, retiré des données du player
SERVER_RESULT_KEYS = frozenset({SERVER_MARK, "screenshot_id"})
MAX_PENDING_PER_SCREEN = 20
HEARTBEAT_COMMANDS_MAX = 10
RESULT_MAX_BYTES = 4096
DEFAULT_CAPABILITIES = ("cmd.reload",)  # vieux player : seul le provisoire est compris


@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "module", "system"]
    id: str | None = None


def capability_of(name: str, args: dict[str, Any]) -> str:
    if name == "restart":
        return f"cmd.restart.{args['scope']}"
    return f"cmd.{name}"


def capabilities_of(last_heartbeat: Any) -> list[str]:
    raw = last_heartbeat.get("capabilities") if isinstance(last_heartbeat, dict) else None
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, str)]
    return list(DEFAULT_CAPABILITIES)


def ref_of(row: PlayerCommand) -> dict[str, Any]:
    """Format commun des commandes poussées (trame `cmd`) et du repli du heartbeat."""
    return {
        "cmd_id": str(row.id),
        "name": row.name,
        "args": dict(row.args or {}),
        "expires_at": row.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }


def _completed(row: PlayerCommand) -> Event:
    payload = sc.PlayerCommandCompletedV1(
        command_id=row.id,
        screen_id=row.screen_id,
        name=row.name,
        status=row.status,  # type: ignore[arg-type]
        code=row.code,
    )
    return Event(
        name="player.command_completed", payload=payload.model_dump(mode="json"), source="core"
    )


def terminate(
    session: AsyncSession,
    row: PlayerCommand,
    status: str,
    code: str | None,
    *,
    result: dict[str, Any] | None = None,
    actor: Actor | None = None,
    now: datetime | None = None,
) -> Event:
    """Passe la commande à un état terminal (audit des échecs, événement `completed`)."""
    now = now or datetime.now(UTC)
    row.status, row.code, row.done_at, row.updated_at = status, code, now, now
    if result is not None:
        row.result = result
    if status in FAILURES:
        audit.record(
            session,
            actor_kind="system",
            actor_id=None,
            action="screen.command.failed",
            target_type="screen",
            target_id=str(row.screen_id) if row.screen_id else None,
            details={"command_id": str(row.id), "name": row.name, "status": status, "code": code},
        )
    elif status == "cancelled" and actor is not None:
        audit.record(
            session,
            actor_kind=actor.kind,
            actor_id=actor.id,
            action="screen.command.cancelled",
            target_type="screen",
            target_id=str(row.screen_id) if row.screen_id else None,
            details={"command_id": str(row.id), "name": row.name},
        )
    return _completed(row)


async def expire_device_commands(
    session: AsyncSession, *, device_id: UUID | None = None, screen_id: UUID | None = None,
    code: str,
) -> list[Event]:  # fmt: skip
    """Périme les commandes en attente d'un appareil (réappairage) ou d'un écran (suppression)."""
    stmt = select(PlayerCommand).where(PlayerCommand.status.in_(PENDING)).with_for_update()
    if device_id is not None:
        stmt = stmt.where(PlayerCommand.device_id == device_id)
    if screen_id is not None:
        stmt = stmt.where(PlayerCommand.screen_id == screen_id)
    rows = (await session.execute(stmt)).scalars().all()
    events = [terminate(session, r, "expired", code) for r in rows]
    if screen_id is not None:
        # Suppression d'écran : on détache la ligne ici plutôt que de laisser les deux SET NULL
        # en cascade (écran puis appareil) se succéder sur une ligne déjà modifiée dans la même
        # transaction : selon l'ordre des déclencheurs, PostgreSQL revérifie alors la FK
        # `device_id` vers un appareil déjà supprimé (violation, 409 `screen.conflict`).
        for r in rows:
            r.screen_id = None
            r.device_id = None
    return events


class CommandCore:
    def __init__(self, session: AsyncSession, bus: EventBus | None) -> None:
        self._s = session
        self._bus = bus

    async def publish(self, events: Iterable[Event]) -> None:
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)

    # --- création ----------------------------------------------------------------------
    async def create(
        self,
        actor: Actor,
        screen: Screen,
        name: str,
        args: dict[str, Any],
        *,
        ttl_s: int | None = None,
        request_key: str | None = None,
        batch_id: UUID | None = None,
    ) -> tuple[PlayerCommand, bool]:
        """(commande, créée) ; `créée=False` : rejeu idempotent ou capture déjà en attente."""
        model = PLAYER_COMMAND_ARGS.get(name)
        if model is None or name not in TTL_S:
            raise ApiError("cmd.unknown", 422)
        if actor.kind == "module" and name in ADMIN_ONLY:
            raise ApiError("cmd.role_required", 403, required="admin")
        try:
            clean = model.model_validate(args).model_dump(mode="json")
        except ValidationError as exc:
            raise ApiError("cmd.args_invalid", 422) from exc
        default, maximum = TTL_S[name]
        ttl = default if ttl_s is None else ttl_s
        if not 10 <= ttl <= maximum:
            raise ApiError("cmd.ttl_invalid", 422, max_s=maximum)
        existing = await self._by_key(actor, request_key)
        if existing is not None:
            return self._replay(existing, screen, name)
        device = (
            await self._s.execute(
                select(ScreenDevice).where(
                    ScreenDevice.screen_id == screen.id,
                    ScreenDevice.revoked_at.is_(None),
                    (ScreenDevice.expires_at.is_(None)) | (ScreenDevice.expires_at > func.now()),
                )
            )
        ).scalar_one_or_none()
        if device is None:
            raise ApiError("cmd.no_device", 409)
        if capability_of(name, clean) not in capabilities_of(device.last_heartbeat):
            raise ApiError("cmd.unsupported", 409)
        now = datetime.now(UTC)
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"pc:{screen.id}"},
        )
        live = (
            (
                await self._s.execute(
                    select(PlayerCommand).where(
                        PlayerCommand.screen_id == screen.id,
                        PlayerCommand.status.in_(PENDING),
                        PlayerCommand.expires_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )
        if name == "screenshot":
            dup = next((r for r in live if r.name == "screenshot"), None)
            if dup is not None:
                await self._s.commit()  # libère le verrou ; `commit` conserve les attributs chargés
                return dup, False
        if len(live) >= MAX_PENDING_PER_SCREEN:
            raise ApiError("cmd.too_many_pending", 429, retry_after_s=30)
        row = PlayerCommand(
            screen_id=screen.id,
            device_id=device.id,
            name=name,
            args=clean,
            status="queued",
            request_key=request_key,
            batch_id=batch_id,
            actor_kind=actor.kind,
            actor_id=actor.id,
            expires_at=now + timedelta(seconds=ttl),
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        audit.record(
            self._s,
            actor_kind=actor.kind,
            actor_id=actor.id,
            action="screen.command.requested",
            target_type="screen",
            target_id=str(screen.id),
            details={
                "command_id": str(row.id),
                "name": name,
                "args": clean,
                "batch_id": str(batch_id) if batch_id else None,
            },
        )
        await notify_raw(self._s, SCREEN_KIND, str(screen.id))
        try:
            await self._s.commit()
        except IntegrityError as exc:  # course sur (actor_id, request_key)
            await self._s.rollback()
            again = await self._by_key(actor, request_key)
            if again is None:
                raise ApiError("cmd.conflict", 409) from exc
            return self._replay(again, screen, name)
        await self.publish(
            [
                Event(
                    name="player.command_requested",
                    payload=sc.PlayerCommandRequestedV1(
                        command_id=row.id,
                        screen_id=screen.id,
                        name=name,
                        actor_kind=actor.kind,
                        batch_id=batch_id,
                    ).model_dump(mode="json"),
                    source="core",
                )
            ]
        )
        return row, True

    async def _by_key(self, actor: Actor, request_key: str | None) -> PlayerCommand | None:
        if request_key is None or actor.id is None:
            return None
        return (
            await self._s.execute(
                select(PlayerCommand).where(
                    PlayerCommand.actor_id == actor.id, PlayerCommand.request_key == request_key
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _replay(row: PlayerCommand, screen: Screen, name: str) -> tuple[PlayerCommand, bool]:
        if row.screen_id != screen.id or row.name != name:
            raise ApiError("cmd.request_key_reused", 409)
        return row, False

    # --- livraison ---------------------------------------------------------------------
    async def pending(
        self, device_id: UUID, *, limit: int = HEARTBEAT_COMMANDS_MAX
    ) -> list[PlayerCommand]:
        """`queued|sent` non expirées, plus anciennes d'abord."""
        return list(
            (
                await self._s.execute(
                    select(PlayerCommand)
                    .where(
                        PlayerCommand.device_id == device_id,
                        PlayerCommand.status.in_(PENDING),
                        PlayerCommand.expires_at > datetime.now(UTC),
                    )
                    .order_by(PlayerCommand.created_at, PlayerCommand.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def mark_sent(self, rows: Sequence[PlayerCommand]) -> None:
        ids = [r.id for r in rows if r.status == "queued"]
        if ids:
            now = datetime.now(UTC)
            await self._s.execute(
                update(PlayerCommand)
                .where(PlayerCommand.id.in_(ids), PlayerCommand.status == "queued")
                .values(status="sent", sent_at=now, updated_at=now)
            )
            await self._s.commit()

    # --- résultats ---------------------------------------------------------------------
    async def apply_results(self, device_id: UUID, results: Sequence[CmdResult]) -> list[UUID]:
        """Applique les résultats du player ; retourne les identifiants à acquitter.

        Une commande inconnue, étrangère à l'appareil ou déjà terminale est ignorée (idempotent)
        mais acquittée : le player la retire de sa file locale.
        """
        events: list[Event] = []
        acked: list[UUID] = []
        for res in results:
            acked.append(res.cmd_id)
            row = (
                await self._s.execute(
                    select(PlayerCommand)
                    .where(PlayerCommand.id == res.cmd_id, PlayerCommand.device_id == device_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.status not in PENDING:
                continue
            data = res.data if len(str(res.data)) <= RESULT_MAX_BYTES else {}
            data = {k: v for k, v in data.items() if k not in SERVER_RESULT_KEYS}
            code = res.code[:80] if res.code else None
            events.append(terminate(self._s, row, res.status, code, result=data or None))
        await self._s.commit()
        await self.publish(events)
        return acked

    # --- annulation --------------------------------------------------------------------
    async def cancel(self, actor: Actor, command_id: UUID) -> PlayerCommand:
        row = (
            await self._s.execute(
                select(PlayerCommand).where(PlayerCommand.id == command_id).with_for_update()
            )
        ).scalar_one()
        if row.status != "queued":
            await self._s.rollback()
            raise ApiError("cmd.not_cancellable", 409)
        event = terminate(self._s, row, "cancelled", None, actor=actor)
        await self._s.commit()
        await self.publish([event])
        return row

    # --- affichage ---------------------------------------------------------------------
    async def requesters(self, rows: Sequence[PlayerCommand]) -> dict[str, str]:
        ids: list[UUID] = []
        for r in rows:
            if r.actor_kind == "user" and r.actor_id:
                try:
                    ids.append(UUID(r.actor_id))
                except ValueError:
                    continue
        if not ids:
            return {}
        found = (
            await self._s.execute(
                select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(ids))
            )
        ).all()
        return {str(i): n for i, n in found}

    async def out(self, rows: Sequence[PlayerCommand]) -> list[sc.PlayerCommandOut]:
        names = await self.requesters(rows)
        return [
            sc.PlayerCommandOut(
                id=r.id,
                screen_id=r.screen_id,
                name=r.name,  # type: ignore[arg-type]
                args=dict(r.args or {}),
                status=r.status,  # type: ignore[arg-type]
                code=r.code,
                requested_by=names.get(r.actor_id or "") if r.actor_kind == "user" else None,
                created_at=r.created_at,
                expires_at=r.expires_at,
                done_at=r.done_at,
                screenshot_id=_shot_id(r),
            )
            for r in rows
        ]


def _shot_id(row: PlayerCommand) -> UUID | None:
    """Seul un résultat posé par l'ingestion serveur (marqueur) désigne une capture."""
    res = row.result
    trusted = isinstance(res, dict) and res.get(SERVER_MARK) is True
    raw = res.get("screenshot_id") if trusted and isinstance(res, dict) else None
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None
