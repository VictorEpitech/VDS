"""Services du parc : règles métier, audit et événements. Les routes ne font que déléguer."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Organization, Schedule, Screen, ScreenGroup, Site
from vds_server.inventory import schemas as sc
from vds_server.player.kiosk import load_requests


class InventoryService:
    def __init__(self, session: AsyncSession, bus: EventBus | None, actor: Principal) -> None:
        self._s = session
        self._bus = bus
        self._actor = actor
        self._events: list[Event] = []

    # --- infrastructure --------------------------------------------------
    def _audit(self, action: str, target_type: str, target_id: UUID, **details: Any) -> None:
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        self._events.append(
            Event(
                name=action,
                payload={"id": str(target_id), **details},
                source="core",
            )
        )

    async def _flush(self, conflict_code: str) -> None:
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError(conflict_code, 409) from exc

    async def _commit(self, conflict_code: str) -> None:
        try:
            await self._s.commit()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError(conflict_code, 409) from exc
        events, self._events = self._events, []
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)

    async def _get[T](self, model: type[T], pk: UUID, code: str) -> T:
        row = await self._s.get(model, pk)
        if row is None:
            raise ApiError(code, 404)
        return row

    # --- organisation ----------------------------------------------------
    async def organization(self) -> Organization:
        org = (await self._s.execute(select(Organization))).scalar_one_or_none()
        if org is None:
            raise ApiError("organization.not_found", 404)
        return org

    async def rename_organization(self, body: sc.OrganizationPatch) -> Organization:
        org = await self.organization()
        org.name = body.name
        self._audit("organization.updated", "organization", org.id, name=body.name)
        await self._commit("organization.conflict")
        return org

    # --- sites -----------------------------------------------------------
    async def list_sites(self, *, include_archived: bool) -> list[Site]:
        q = select(Site).order_by(Site.name)
        if not include_archived:
            q = q.where(Site.archived_at.is_(None))
        return list((await self._s.execute(q)).scalars())

    async def get_site(self, site_id: UUID) -> Site:
        return await self._get(Site, site_id, "site.not_found")

    async def create_site(self, body: sc.SiteCreate) -> Site:
        org = await self.organization()
        site = Site(organization_id=org.id, name=body.name, slug=body.slug)
        self._s.add(site)
        await self._flush("site.slug_taken")
        self._audit("site.created", "site", site.id, slug=site.slug)
        await self._commit("site.slug_taken")
        return site

    async def update_site(self, site_id: UUID, body: sc.SitePatch) -> Site:
        site = await self.get_site(site_id)
        if body.name is not None:
            site.name = body.name
        if body.slug is not None:
            site.slug = body.slug
        if body.archived is not None:
            site.archived_at = datetime.now(UTC) if body.archived else None
        site.updated_at = datetime.now(UTC)
        self._audit("site.updated", "site", site.id, fields=sorted(body.model_fields_set))
        await self._commit("site.slug_taken")
        return site

    async def delete_site(self, site_id: UUID) -> None:
        site = await self.get_site(site_id)
        groups = await self._count(ScreenGroup, ScreenGroup.site_id == site_id)
        screens = await self._count(Screen, Screen.site_id == site_id)
        if groups or screens:
            raise ApiError("site.not_empty", 409, groups=groups, screens=screens)
        dropped = await self._drop_schedules("site", site_id)
        await self._s.delete(site)
        self._audit("site.deleted", "site", site_id, slug=site.slug, schedules_deleted=dropped)
        await self._commit("site.not_empty")

    async def _drop_schedules(self, target_type: str, target_id: UUID) -> int:
        """Une programmation ne survit pas à sa cible (pas de FK polymorphe)."""
        res = await self._s.execute(
            delete(Schedule).where(
                Schedule.target_type == target_type, Schedule.target_id == target_id
            )
        )
        return int(res.rowcount or 0)  # type: ignore[attr-defined]

    async def _count(self, model: Any, *where: Any) -> int:
        return int(
            (
                await self._s.execute(select(func.count()).select_from(model).where(*where))
            ).scalar_one()
        )

    # --- groupes ---------------------------------------------------------
    async def list_groups(self, site_id: UUID) -> list[ScreenGroup]:
        await self.get_site(site_id)
        q = select(ScreenGroup).where(ScreenGroup.site_id == site_id).order_by(ScreenGroup.name)
        return list((await self._s.execute(q)).scalars())

    async def get_group(self, group_id: UUID) -> ScreenGroup:
        return await self._get(ScreenGroup, group_id, "group.not_found")

    async def create_group(self, site_id: UUID, body: sc.GroupCreate) -> ScreenGroup:
        await self.get_site(site_id)
        group = ScreenGroup(site_id=site_id, name=body.name)
        self._s.add(group)
        await self._flush("group.name_taken")
        self._audit("group.created", "group", group.id, site_id=str(site_id))
        await self._commit("group.name_taken")
        return group

    async def update_group(self, group_id: UUID, body: sc.GroupPatch) -> ScreenGroup:
        group = await self.get_group(group_id)
        group.name = body.name
        group.updated_at = datetime.now(UTC)
        self._audit("group.updated", "group", group.id, fields=["name"])
        await self._commit("group.name_taken")
        return group

    async def delete_group(self, group_id: UUID) -> None:
        group = await self.get_group(group_id)
        screens = await self._count(Screen, Screen.group_id == group_id)
        if screens:
            raise ApiError("group.not_empty", 409, screens=screens)
        dropped = await self._drop_schedules("group", group_id)
        await self._s.delete(group)
        self._audit(
            "group.deleted",
            "group",
            group_id,
            site_id=str(group.site_id),
            schedules_deleted=dropped,
        )
        await self._commit("group.not_empty")

    # --- écrans ----------------------------------------------------------
    async def list_screens(
        self, *, site_id: UUID | None, group_id: UUID | None, include_archived: bool
    ) -> list[Screen]:
        q = select(Screen).order_by(Screen.name)
        if site_id:
            q = q.where(Screen.site_id == site_id)
        if group_id:
            q = q.where(Screen.group_id == group_id)
        if not include_archived:
            q = q.where(Screen.archived_at.is_(None))
        return list((await self._s.execute(q)).scalars())

    async def get_screen(self, screen_id: UUID) -> Screen:
        return await self._get(Screen, screen_id, "screen.not_found")

    async def _check_group(self, group_id: UUID | None, site_id: UUID) -> None:
        if group_id is None:
            return
        group = await self.get_group(group_id)
        if group.site_id != site_id:
            raise ApiError("screen.group_other_site", 422)

    async def create_screen(self, body: sc.ScreenCreate) -> Screen:
        await self.get_site(body.site_id)
        await self._check_group(body.group_id, body.site_id)
        screen = Screen(
            site_id=body.site_id,
            group_id=body.group_id,
            name=body.name,
            orientation=body.orientation,
        )
        self._s.add(screen)
        await self._flush("screen.conflict")
        self._audit("screen.created", "screen", screen.id, site_id=str(body.site_id))
        await self._commit("screen.conflict")
        return screen

    async def update_screen(self, screen_id: UUID, body: sc.ScreenPatch) -> Screen:
        screen = await self.get_screen(screen_id)
        moved: tuple[UUID | None, UUID | None, str, str] | None = None
        if "group_id" in body.model_fields_set:
            await self._check_group(body.group_id, screen.site_id)
            if body.group_id != screen.group_id:
                before = (await load_requests(self._s, [screen]))[screen.id].mode
                old = screen.group_id
                screen.group_id = body.group_id
                after = (await load_requests(self._s, [screen]))[screen.id].mode
                if before != after:
                    moved = (old, body.group_id, before, after)
            else:
                screen.group_id = body.group_id
        if body.name is not None:
            screen.name = body.name
        if body.orientation is not None:
            screen.orientation = body.orientation
        if body.archived is not None:
            screen.archived_at = datetime.now(UTC) if body.archived else None
        screen.updated_at = datetime.now(UTC)
        self._audit("screen.updated", "screen", screen.id, fields=sorted(body.model_fields_set))
        if moved is not None:  # le déplacement change le mode kiosque effectif de l'écran
            g_old, g_new, was, now = moved
            self._audit(
                f"screen.kiosk.{now}",
                "screen",
                screen.id,
                scope_level="screen",
                cause="group_move",
                effective=now,
                previous=was,
                from_group=str(g_old) if g_old else None,
                to_group=str(g_new) if g_new else None,
            )
        await self._commit("screen.conflict")
        return screen

    async def delete_screen(self, screen_id: UUID) -> None:
        screen = await self.get_screen(screen_id)
        dropped = await self._drop_schedules("screen", screen_id)
        await self._s.delete(screen)
        self._audit(
            "screen.deleted",
            "screen",
            screen_id,
            site_id=str(screen.site_id),
            schedules_deleted=dropped,
        )
        await self._commit("screen.conflict")
