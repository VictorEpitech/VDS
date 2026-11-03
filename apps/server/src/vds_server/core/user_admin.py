"""Gestion des utilisateurs et de leur périmètre (lot J4) : rôle, activation, nœuds de périmètre.

Admin seul. Codes d'erreur stables ; aucune chaîne destinée à l'utilisateur. Le rôle n'est
modifiable que si `role_source = 'vds'` ; le périmètre est toujours stocké dans VDS (J-D7).
"""

import hashlib
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.perimeter import MAX_NODES, NodeRef, NodeType
from vds_server.db.models import Organization, ScreenGroup, Site, UserAccount, UserScope
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.scope import PerimeterScope

Role = Literal["admin", "editor"]
RoleSource = Literal["vds", "provider"]


# --- schémas ------------------------------------------------------------------------------
class ScopeNodeOut(BaseModel):
    type: NodeType
    id: UUID
    name: str


class PerimeterOut(BaseModel):
    unrestricted: bool
    can_share: bool  # option « Partagé » proposée (J-D11) : = unrestricted
    nodes: list[ScopeNodeOut]


class UserOut(BaseModel):
    id: UUID
    display_name: str
    email: str | None
    role: Role
    role_source: RoleSource
    is_active: bool
    last_seen_at: datetime | None
    scopes: list[ScopeNodeOut]
    scopes_version: str  # valeur de l'ETag (If-Match de PUT scopes)


class UserPage(BaseModel):
    items: list[UserOut]
    total: int


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role | None = None
    is_active: bool | None = None


class ScopesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[NodeRef]


class ScopeTreeGroup(BaseModel):
    id: UUID
    name: str
    writable: bool


class ScopeTreeSite(BaseModel):
    id: UUID
    name: str
    writable: bool
    groups: list[ScopeTreeGroup]


class ScopeTree(BaseModel):
    can_share: bool
    sites: list[ScopeTreeSite]


# --- helpers ------------------------------------------------------------------------------
def scopes_version(nodes: list[ScopeNodeOut] | list[NodeRef]) -> str:
    raw = "|".join(sorted(f"{n.type}:{n.id}" for n in nodes))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def if_match_value(value: str | None) -> str:
    """`If-Match: "<version>"` (guillemets et préfixe faible tolérés) ; 428 si absent."""
    if value is None or not value.strip():
        raise ApiError("user.if_match_required", 428)
    return value.strip().removeprefix("W/").strip('"')


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def scope_nodes(
    session: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, list[ScopeNodeOut]]:
    """Nœuds de périmètre (avec nom) par utilisateur, triés par type puis nom."""
    out: dict[UUID, list[ScopeNodeOut]] = {u: [] for u in user_ids}
    if not user_ids:
        return out
    org = (
        select(UserScope.user_id, UserScope.scope_type, UserScope.scope_id, Organization.name)
        .join(Organization, Organization.id == UserScope.organization_id)
        .where(UserScope.user_id.in_(user_ids))
    )
    site = (
        select(UserScope.user_id, UserScope.scope_type, UserScope.scope_id, Site.name)
        .join(Site, Site.id == UserScope.site_id)
        .where(UserScope.user_id.in_(user_ids))
    )
    group = (
        select(UserScope.user_id, UserScope.scope_type, UserScope.scope_id, ScreenGroup.name)
        .join(ScreenGroup, ScreenGroup.id == UserScope.group_id)
        .where(UserScope.user_id.in_(user_ids))
    )
    order = {"organization": 0, "site": 1, "group": 2}
    rows = [r for q in (org, site, group) for r in (await session.execute(q)).all()]
    for user_id, kind, scope_id, name in sorted(rows, key=lambda r: (order[r[1]], r[3], str(r[2]))):
        out[user_id].append(ScopeNodeOut(type=cast(NodeType, kind), id=scope_id, name=name))
    return out


def _user_out(row: UserAccount, nodes: list[ScopeNodeOut]) -> UserOut:
    return UserOut(
        id=row.id,
        display_name=row.display_name,
        email=row.email,
        role="admin" if row.role == "admin" else "editor",
        role_source="provider" if row.role_source == "provider" else "vds",
        is_active=row.is_active,
        last_seen_at=row.last_seen_at,
        scopes=nodes,
        scopes_version=scopes_version(nodes),
    )


async def describe_perimeter(session: AsyncSession, principal: Principal) -> PerimeterOut:
    nodes = (await scope_nodes(session, [principal.user_id]))[principal.user_id]
    unrestricted = principal.perimeter.unrestricted
    return PerimeterOut(unrestricted=unrestricted, can_share=unrestricted, nodes=nodes)


async def role_source_of(session: AsyncSession, user_id: UUID) -> RoleSource:
    value = await session.scalar(select(UserAccount.role_source).where(UserAccount.id == user_id))
    return "provider" if value == "provider" else "vds"


# --- service ------------------------------------------------------------------------------
class UserAdminService(ServiceBase):
    def __init__(self, session: AsyncSession, bus: EventBus | None, actor: Principal) -> None:
        super().__init__(session, bus, actor)
        self._scope = PerimeterScope(session)

    async def _row(self, user_id: UUID, *, lock: bool = False) -> UserAccount:
        q = select(UserAccount).where(UserAccount.id == user_id)
        if lock:
            q = q.with_for_update()
        row = (
            await self._s.execute(q.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("user.not_found", 404)
        return row

    async def list_page(
        self, *, q: str | None, role: Role | None, site_id: UUID | None, limit: int, offset: int
    ) -> UserPage:
        flt = []
        if q:
            like = f"%{escape_like(q.strip())}%"
            flt.append(
                or_(
                    UserAccount.display_name.ilike(like, escape="\\"),
                    UserAccount.email.ilike(like, escape="\\"),
                )
            )
        if role is not None:
            flt.append(UserAccount.role == role)
        if site_id is not None:
            # utilisateurs qui voient le site : admins, nœud organisation, site ou groupe du site
            group_ids = select(ScreenGroup.id).where(ScreenGroup.site_id == site_id)
            reach = select(UserScope.user_id).where(
                or_(
                    UserScope.scope_type == "organization",
                    UserScope.site_id == site_id,
                    UserScope.group_id.in_(group_ids),
                )
            )
            flt.append(or_(UserAccount.role == "admin", UserAccount.id.in_(reach)))
        total = await self._s.scalar(select(func.count()).select_from(UserAccount).where(*flt))
        rows = list(
            (
                await self._s.execute(
                    select(UserAccount)
                    .where(*flt)
                    .order_by(func.lower(UserAccount.display_name), UserAccount.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
        )
        nodes = await scope_nodes(self._s, [r.id for r in rows])
        return UserPage(items=[_user_out(r, nodes[r.id]) for r in rows], total=total or 0)

    async def get(self, user_id: UUID) -> UserOut:
        row = await self._row(user_id)
        return _user_out(row, (await scope_nodes(self._s, [row.id]))[row.id])

    async def _other_active_admins(self, user_id: UUID) -> int:
        q = (
            select(func.count())
            .select_from(UserAccount)
            .where(UserAccount.role == "admin", UserAccount.is_active, UserAccount.id != user_id)
        )
        return await self._s.scalar(q) or 0

    async def patch(self, user_id: UUID, body: UserPatch) -> UserOut:
        # verrou sur les admins : deux rétrogradations simultanées ne vident pas l'organisation
        await self._s.execute(
            select(UserAccount.id).where(UserAccount.role == "admin").with_for_update()
        )
        row = await self._row(user_id, lock=True)
        new_role = body.role if body.role is not None else row.role
        role_changes = new_role != row.role
        if role_changes and row.role_source == "provider":
            raise ApiError("user.role_managed_by_provider", 409)
        self_target = row.id == self._actor.user_id
        if role_changes and self_target and new_role != "admin":
            raise ApiError("user.self_demotion", 422)
        new_active = body.is_active if body.is_active is not None else row.is_active
        if self_target and not new_active:
            raise ApiError("user.self_deactivation", 422)
        was_admin = row.role == "admin" and row.is_active
        will_be_admin = new_role == "admin" and new_active
        if was_admin and not will_be_admin and await self._other_active_admins(row.id) == 0:
            raise ApiError("user.last_admin", 409)
        if role_changes:
            old_role, row.role = row.role, new_role
            audit.record(
                self._s,
                actor_kind="user",
                actor_id=self._actor.actor_id,
                action="user.role_changed",
                target_type="user",
                target_id=str(row.id),
                details={"from": old_role, "to": new_role},
            )
            self._events.append(
                Event(
                    name="user.role_changed",
                    payload={
                        "version": 1,
                        "user_id": str(row.id),
                        "from_role": old_role,
                        "to_role": new_role,
                        "source": row.role_source,
                    },
                    source="core",
                )
            )
        if new_active != row.is_active:
            row.is_active = new_active
            self._audit(
                "user.activated" if new_active else "user.deactivated",
                "user",
                row.id,
                "user_id",
                emit=False,
            )
        await self._commit("user.conflict")
        return await self.get(user_id)

    async def _validate_nodes(self, nodes: list[NodeRef]) -> None:
        for kind, model in (("site", Site), ("group", ScreenGroup)):
            ids = {n.id for n in nodes if n.type == kind}
            if not ids:
                continue
            found = set((await self._s.scalars(select(model.id).where(model.id.in_(ids)))).all())
            for missing in sorted(ids - found, key=str):
                raise ApiError("scope.node_unknown", 422, type=kind, id=str(missing))
        org_ids = {n.id for n in nodes if n.type == "organization"}
        if org_ids:
            found_org = set(
                (
                    await self._s.scalars(
                        select(Organization.id).where(Organization.id.in_(org_ids))
                    )
                ).all()
            )
            for missing in sorted(org_ids - found_org, key=str):
                raise ApiError("scope.node_unknown", 422, type="organization", id=str(missing))

    async def put_scopes(self, user_id: UUID, body: ScopesIn, if_match: str | None) -> UserOut:
        wanted = if_match_value(if_match)
        unique = list({(n.type, n.id): n for n in body.scopes}.values())
        if len(unique) > MAX_NODES:
            raise ApiError("scope.too_many_nodes", 422, max=MAX_NODES)
        row = await self._row(user_id, lock=True)  # sérialise les remplacements concurrents
        current = (await scope_nodes(self._s, [row.id]))[row.id]
        if scopes_version(current) != wanted:
            raise ApiError("user.scopes_conflict", 412, version=scopes_version(current))
        await self._validate_nodes(unique)
        await self._s.execute(delete(UserScope).where(UserScope.user_id == row.id))
        for n in unique:
            self._s.add(
                UserScope(
                    user_id=row.id,
                    organization_id=n.id if n.type == "organization" else None,
                    site_id=n.id if n.type == "site" else None,
                    group_id=n.id if n.type == "group" else None,
                    created_by=self._actor.user_id,
                )
            )
        refs = [{"type": n.type, "id": str(n.id)} for n in unique]
        self._audit("user.scope_changed", "user", row.id, "user_id", emit=False, scopes=refs)
        self._events.append(
            Event(
                name="user.scope_changed",
                payload={
                    "version": 1,
                    "user_id": str(row.id),
                    "scopes": refs,
                    "actor_id": self._actor.actor_id,
                },
                source="core",
            )
        )
        await self._commit("user.scopes_conflict")
        return await self.get(user_id)

    async def scope_tree(self) -> ScopeTree:
        p = self._actor.perimeter
        sites = list(
            (
                await self._s.execute(
                    select(Site)
                    .where(self._scope.site_filter(self._actor), Site.archived_at.is_(None))
                    .order_by(Site.name)
                )
            ).scalars()
        )
        groups = list(
            (
                await self._s.execute(
                    select(ScreenGroup)
                    .where(
                        self._scope.group_filter(self._actor),
                        ScreenGroup.site_id.in_([s.id for s in sites]),
                    )
                    .order_by(ScreenGroup.name)
                )
            ).scalars()
        )
        by_site: dict[UUID, list[ScopeTreeGroup]] = {s.id: [] for s in sites}
        for g in groups:
            by_site[g.site_id].append(
                ScopeTreeGroup(
                    id=g.id, name=g.name, writable=p.unrestricted or g.id in p.groups_closure
                )
            )
        return ScopeTree(
            can_share=p.unrestricted,
            sites=[
                ScopeTreeSite(
                    id=s.id,
                    name=s.name,
                    writable=p.unrestricted or s.id in p.sites,
                    groups=by_site[s.id],
                )
                for s in sites
            ],
        )
