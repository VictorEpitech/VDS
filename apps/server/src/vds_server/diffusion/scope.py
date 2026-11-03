"""Périmètre de contenu : point unique d'accès aux médias et playlists.

V1 : « organisation entière » (rôles admin/editor, organisation unique). Le lot J remplacera
l'implémentation (périmètre site/groupe) sans toucher aux appelants ; jamais d'ACL par objet.
"""

from typing import Protocol
from uuid import UUID

from sqlalchemy import ColumnElement, true

from vds_server.core.auth import Principal


class ContentScope(Protocol):
    def media_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    def screen_filter(self, principal: Principal) -> ColumnElement[bool]: ...

    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool: ...

    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool: ...


class OrganizationScope:
    def media_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    def screen_filter(self, principal: Principal) -> ColumnElement[bool]:
        return true()

    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool:
        return True

    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool:
        return True
