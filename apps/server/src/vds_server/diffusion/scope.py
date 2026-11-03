"""Périmètre de contenu : point unique d'accès aux contenus, écrans et cibles de diffusion.

`PerimeterScope` (lot J) applique le périmètre porté par `Principal.perimeter` ; jamais d'ACL par
objet. `OrganizationScope` (tout vrai) est conservé pour les tests et les outils.
"""

from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.perimeter import Node
from vds_server.db.models import Media, Playlist, Screen, ScreenGroup, Site
from vds_server.db.models_layout import Layout

Access = Literal["read", "write"]
Col = Any  # colonne SQLAlchemy (attribut ORM ou expression), typage trop étroit sinon


class ContentScope(Protocol):
    def media_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def screen_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def layout_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    async def can_write_layout(self, principal: Principal, layout_id: UUID) -> bool: ...

    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool: ...

    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool: ...

    # lot J
    def owner_filter(
        self,
        principal: Principal,
        site_col: Col,
        group_col: Col,
        access: Access,
    ) -> ColumnElement[bool]: ...

    def target_filter(
        self,
        principal: Principal,
        type_col: Col,
        id_col: Col,
        access: Access,
    ) -> ColumnElement[bool]: ...

    def site_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def group_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def can_own(self, principal: Principal, owner: Node | None) -> bool: ...

    def default_owner(self, principal: Principal) -> Node | None | Literal["ask"]: ...

    async def can_target(self, principal: Principal, target_type: str, target_id: UUID) -> bool: ...

    async def can_read_target(
        self, principal: Principal, target_type: str, target_id: UUID
    ) -> bool: ...


class OrganizationScope:
    def media_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def screen_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def layout_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    async def can_write_layout(self, principal: Principal, layout_id: UUID) -> bool:
        return True

    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool:
        return True

    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool:
        return True

    def owner_filter(
        self,
        principal: Principal,
        site_col: Col,
        group_col: Col,
        access: Access,
    ) -> ColumnElement[bool]:
        return true()

    def target_filter(
        self,
        principal: Principal,
        type_col: Col,
        id_col: Col,
        access: Access,
    ) -> ColumnElement[bool]:
        return true()

    def site_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def group_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def can_own(self, principal: Principal, owner: Node | None) -> bool:
        return True

    def default_owner(self, principal: Principal) -> Node | None | Literal["ask"]:
        return None

    async def can_target(self, principal: Principal, target_type: str, target_id: UUID) -> bool:
        return True

    async def can_read_target(
        self, principal: Principal, target_type: str, target_id: UUID
    ) -> bool:
        return True


class PerimeterScope:
    """Implémentation du lot J : lecture = ancêtres + égal + descendants, écriture = égal +
    descendants ; le contenu partagé (propriétaire NULL) n'est modifiable que par `unrestricted`."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- filtres SQL -------------------------------------------------------------------
    def owner_filter(
        self,
        principal: Principal,
        site_col: Col,
        group_col: Col,
        access: Access,
    ) -> ColumnElement[bool]:
        p = principal.perimeter
        if p.unrestricted:
            return true()
        if p.empty:
            return false()
        if access == "read":
            return or_(
                site_col.is_(None),
                and_(group_col.is_(None), site_col.in_(p.sites_closure)),
                group_col.in_(p.groups_closure),
            )
        return or_(
            and_(group_col.is_(None), site_col.in_(p.sites)),
            group_col.in_(p.groups_closure),
        )

    def screen_filter(self, principal: Principal) -> ColumnElement[bool]:
        p = principal.perimeter
        if p.unrestricted:
            return true()
        if p.empty:
            return false()
        return or_(Screen.site_id.in_(p.sites), Screen.group_id.in_(p.groups))

    def target_filter(
        self,
        principal: Principal,
        type_col: Col,
        id_col: Col,
        access: Access,
    ) -> ColumnElement[bool]:
        p = principal.perimeter
        if p.unrestricted:
            return true()
        if p.empty:
            return false()
        screens = id_col.in_(select(Screen.id).where(self.screen_filter(principal)))
        if access == "read":
            return or_(
                type_col == "organization",
                and_(type_col == "site", id_col.in_(p.sites_closure)),
                and_(type_col == "group", id_col.in_(p.groups_closure)),
                and_(type_col == "screen", screens),
            )
        return or_(
            and_(type_col == "site", id_col.in_(p.sites)),
            and_(type_col == "group", id_col.in_(p.groups_closure)),
            and_(type_col == "screen", screens),
        )

    def media_filter(self, principal: Principal) -> ColumnElement[bool]:
        return self.owner_filter(principal, Media.owner_site_id, Media.owner_group_id, "read")

    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]:
        return self.owner_filter(principal, Playlist.owner_site_id, Playlist.owner_group_id, "read")

    def layout_filter(self, principal: Principal) -> ColumnElement[bool]:
        return self.owner_filter(principal, Layout.owner_site_id, Layout.owner_group_id, "read")

    def site_filter(self, principal: Principal) -> ColumnElement[bool]:
        p = principal.perimeter
        if p.unrestricted:
            return true()
        return Site.id.in_(p.sites_closure) if not p.empty else false()

    def group_filter(self, principal: Principal) -> ColumnElement[bool]:
        p = principal.perimeter
        if p.unrestricted:
            return true()
        return ScreenGroup.id.in_(p.groups_closure) if not p.empty else false()

    # --- écriture ----------------------------------------------------------------------
    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool:
        flt = self.owner_filter(principal, Media.owner_site_id, Media.owner_group_id, "write")
        return bool(await self._s.scalar(select(exists().where(Media.id == media_id, flt))))

    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool:
        flt = self.owner_filter(principal, Playlist.owner_site_id, Playlist.owner_group_id, "write")
        return bool(await self._s.scalar(select(exists().where(Playlist.id == playlist_id, flt))))

    async def can_write_layout(self, principal: Principal, layout_id: UUID) -> bool:
        flt = self.owner_filter(principal, Layout.owner_site_id, Layout.owner_group_id, "write")
        return bool(await self._s.scalar(select(exists().where(Layout.id == layout_id, flt))))

    def can_own(self, principal: Principal, owner: Node | None) -> bool:
        p = principal.perimeter
        if p.unrestricted:
            return True
        if owner is None or p.empty:
            return False
        if owner.type == "site":
            return owner.id in p.sites
        if owner.type == "group":
            return owner.id in p.groups_closure
        return False

    def default_owner(self, principal: Principal) -> Node | None | Literal["ask"]:
        p = principal.perimeter
        if p.unrestricted:
            return None
        nodes = [Node("site", i) for i in sorted(p.sites, key=str)] + [
            Node("group", i) for i in sorted(p.groups, key=str)
        ]
        return nodes[0] if len(nodes) == 1 else "ask"

    async def can_target(self, principal: Principal, target_type: str, target_id: UUID) -> bool:
        p = principal.perimeter
        if p.unrestricted:
            return True
        if p.empty or target_type == "organization":
            return False
        if target_type == "site":
            return target_id in p.sites
        if target_type == "group":
            return target_id in p.groups_closure
        return await self._screen_visible(principal, target_id)

    async def can_read_target(
        self, principal: Principal, target_type: str, target_id: UUID
    ) -> bool:
        p = principal.perimeter
        if p.unrestricted:
            return True
        if p.empty:
            return False
        if target_type == "organization":
            return True
        if target_type == "site":
            return target_id in p.sites_closure
        if target_type == "group":
            return target_id in p.groups_closure
        return await self._screen_visible(principal, target_id)

    async def _screen_visible(self, principal: Principal, screen_id: UUID) -> bool:
        flt = self.screen_filter(principal)
        return bool(await self._s.scalar(select(exists().where(Screen.id == screen_id, flt))))
