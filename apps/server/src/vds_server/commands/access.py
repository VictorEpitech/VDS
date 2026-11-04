"""Commandes et captures côté administrateur : périmètre (lot J) puis rôle par commande."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.agents.queue import CommandQueue
from vds_server.commands import schemas as sc
from vds_server.commands.restart_app import RestartContext
from vds_server.commands.service import ADMIN_ONLY, Actor, CommandCore
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen
from vds_server.db.models_command import PlayerCommand, Screenshot
from vds_server.diffusion.scope import PerimeterScope

IDS_MAX = 200


def screenshot_out(row: Screenshot) -> sc.ScreenshotOut:
    return sc.ScreenshotOut(
        id=row.id,
        screen_id=row.screen_id,
        trigger=row.trigger,  # type: ignore[arg-type]
        captured_at=row.captured_at,
        width=row.width,
        height=row.height,
        bytes=row.bytes,
        image_url=f"/api/v1/screenshots/{row.id}/image",
    )


class CommandAdmin:
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        restart: RestartContext | None = None,
        queue: CommandQueue | None = None,
    ) -> None:
        self._s = session
        self._actor = actor
        self._scope = PerimeterScope(session)
        self.core = CommandCore(session, bus, restart, queue)

    async def screen(self, screen_id: UUID) -> Screen:
        """404 sans nom hors périmètre (jamais 403 : l'existence ne fuit pas)."""
        row = (
            await self._s.execute(
                select(Screen).where(Screen.id == screen_id, self._scope.screen_filter(self._actor))
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("screen.not_found", 404)
        return row

    def _need_role(self, name: str) -> None:
        if name in ADMIN_ONLY and self._actor.role != "admin":
            raise ApiError("cmd.role_required", 403, required="admin")

    async def create(
        self, screen_id: UUID, body: sc.PlayerCommandCreate
    ) -> tuple[sc.PlayerCommandOut, bool]:
        screen = await self.screen(screen_id)
        self._need_role(body.name)
        row, created = await self.core.create(
            Actor("user", self._actor.actor_id),
            screen,
            body.name,
            body.args,
            ttl_s=body.ttl_s,
            request_key=body.request_key,
            batch_id=body.batch_id,
        )
        return (await self.core.out([row]))[0], created

    async def recent(self, screen_id: UUID, limit: int) -> list[sc.PlayerCommandOut]:
        await self.screen(screen_id)
        rows = (
            (
                await self._s.execute(
                    select(PlayerCommand)
                    .where(PlayerCommand.screen_id == screen_id)
                    .order_by(PlayerCommand.created_at.desc(), PlayerCommand.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return await self.core.out(rows)

    async def poll(self, raw_ids: str) -> list[sc.PlayerCommandOut]:
        parts = [p for p in raw_ids.split(",") if p]
        if not parts or len(parts) > IDS_MAX:
            raise ApiError("cmd.ids_invalid", 422, max=IDS_MAX)
        try:
            ids = [UUID(p) for p in parts]
        except ValueError as exc:
            raise ApiError("cmd.ids_invalid", 422, max=IDS_MAX) from exc
        stmt = select(PlayerCommand).where(PlayerCommand.id.in_(ids))
        if not self._actor.perimeter.unrestricted:  # ids hors périmètre : omis, jamais d'erreur
            stmt = stmt.join(Screen, Screen.id == PlayerCommand.screen_id).where(
                self._scope.screen_filter(self._actor)
            )
        rows = (await self._s.execute(stmt.order_by(PlayerCommand.created_at))).scalars().all()
        return await self.core.out(rows)

    async def cancel(self, screen_id: UUID, command_id: UUID) -> sc.PlayerCommandOut:
        await self.screen(screen_id)
        row = (
            await self._s.execute(
                select(PlayerCommand).where(
                    PlayerCommand.id == command_id, PlayerCommand.screen_id == screen_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("cmd.not_found", 404)
        self._need_role(row.name)  # même rôle que la demande
        row = await self.core.cancel(Actor("user", self._actor.actor_id), command_id)
        return (await self.core.out([row]))[0]

    async def screenshots(self, screen_id: UUID, limit: int) -> list[sc.ScreenshotOut]:
        await self.screen(screen_id)
        rows = (
            (
                await self._s.execute(
                    select(Screenshot)
                    .where(Screenshot.screen_id == screen_id)
                    .order_by(Screenshot.captured_at.desc(), Screenshot.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [screenshot_out(r) for r in rows]

    async def screenshot(self, screenshot_id: UUID) -> Screenshot:
        row = (
            await self._s.execute(
                select(Screenshot)
                .join(Screen, Screen.id == Screenshot.screen_id)
                .where(Screenshot.id == screenshot_id, self._scope.screen_filter(self._actor))
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("screenshot.not_found", 404)
        return row
