"""Propriétaire des contenus (lot J) : résolution à la création, invariant J-D5, changement.

Un contenu (média, playlist, mise en page) a un nœud propriétaire (site ou groupe ; NULL/NULL =
organisation, « partagé »). Un objet ne référence que du contenu dont le propriétaire est égal ou
ancêtre de son propre nœud (J-D5), pour tous les principaux, admin compris.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.perimeter import Node, NodeRef, covers, node_of_owner, node_of_target
from vds_server.db.models import (
    Media,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScreenGroup,
    Site,
)
from vds_server.db.models_layout import Layout, LayoutZone
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion.scope import ContentScope

Kind = Literal["media", "playlist", "layout"]
OwnerColumns = tuple[UUID | None, UUID | None]


async def resolve_node(
    session: AsyncSession, ref: NodeRef | None
) -> tuple[Node | None, OwnerColumns]:
    """Référence de requête -> (nœud, colonnes). 404 `scope.node_not_found` si inexistant."""
    if ref is None or ref.type == "organization":
        return None, (None, None)
    if ref.type == "site":
        if await session.scalar(select(Site.id).where(Site.id == ref.id)) is None:
            raise ApiError("scope.node_not_found", 404)
        return Node("site", ref.id), (ref.id, None)
    site_id = await session.scalar(select(ScreenGroup.site_id).where(ScreenGroup.id == ref.id))
    if site_id is None:
        raise ApiError("scope.node_not_found", 404)
    return Node("group", ref.id), (site_id, ref.id)


def _visible_node(actor: Principal, node: Node) -> bool:
    p = actor.perimeter
    if p.unrestricted:
        return True
    return node.id in (p.sites_closure if node.type == "site" else p.groups_closure)


def require_ownable(scope: ContentScope, actor: Principal, node: Node | None) -> None:
    if scope.can_own(actor, node):
        return
    if node is None:
        raise ApiError("scope.shared_forbidden", 403)
    if not _visible_node(actor, node):
        raise ApiError("scope.node_not_found", 404)
    raise ApiError("scope.forbidden", 403)


async def owner_for_create(
    session: AsyncSession,
    scope: ContentScope,
    actor: Principal,
    *,
    given: bool,
    ref: NodeRef | None,
) -> OwnerColumns:
    """Propriétaire d'un nouveau contenu : explicite (`given`) ou défaut du périmètre."""
    if given:
        node, cols = await resolve_node(session, ref)
    else:
        default = scope.default_owner(actor)
        if default == "ask":
            if actor.perimeter.empty:
                raise ApiError("scope.forbidden", 403)
            raise ApiError("scope.owner_required", 422)
        node = default
        cols = (None, None) if node is None else await _cols(session, node)
    require_ownable(scope, actor, node)
    return cols


async def _cols(session: AsyncSession, node: Node) -> OwnerColumns:
    return (await resolve_node(session, NodeRef(type=node.type, id=node.id)))[1]


async def check_reference(session: AsyncSession, owner: Node | None, holder: Node | None) -> None:
    """J-D5 : le propriétaire du contenu référencé couvre le nœud du référent (422)."""
    if not await covers(session, owner, holder):
        raise ApiError("scope.reference_out_of_scope", 422)


async def _schedule_nodes(
    session: AsyncSession, column: object, content_id: UUID
) -> list[Node | None]:
    targets: set[tuple[str, UUID]] = set()
    rows = await session.execute(
        select(Schedule.target_type, Schedule.target_id).where(column == content_id)  # type: ignore[arg-type]
    )
    targets |= {(t, i) for t, i in rows}
    return [await node_of_target(session, t, i) for t, i in sorted(targets, key=str)]


async def _override_nodes(
    session: AsyncSession, column: object, content_id: UUID
) -> list[Node | None]:
    rows = await session.execute(
        select(PlaybackOverride.target_type, PlaybackOverride.target_id).where(
            column == content_id  # type: ignore[arg-type]
        )
    )
    targets = {(t, i) for t, i in rows}
    return [await node_of_target(session, t, i) for t, i in sorted(targets, key=str)]


async def referent_nodes(session: AsyncSession, kind: Kind, content_id: UUID) -> list[Node | None]:
    """Nœuds de tous les objets qui référencent ce contenu (compteur seul exposé)."""
    out: list[Node | None] = []
    if kind == "media":
        rows = await session.execute(
            select(Playlist.owner_site_id, Playlist.owner_group_id)
            .join(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
            .join(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
            .where(PlaylistItemMedia.media_id == content_id)
            .distinct()
        )
        out += [node_of_owner(s, g) for s, g in rows]
        rows = await session.execute(
            select(Layout.owner_site_id, Layout.owner_group_id).where(
                Layout.background_media_id == content_id
            )
        )
        out += [node_of_owner(s, g) for s, g in rows]
    elif kind == "playlist":
        rows = await session.execute(
            select(Layout.owner_site_id, Layout.owner_group_id)
            .join(LayoutZone, LayoutZone.layout_id == Layout.id)
            .where(LayoutZone.playlist_id == content_id)
            .distinct()
        )
        out += [node_of_owner(s, g) for s, g in rows]
        out += await _schedule_nodes(session, Schedule.playlist_id, content_id)
        out += await _override_nodes(session, PlaybackOverride.playlist_id, content_id)
    else:
        out += await _schedule_nodes(session, Schedule.layout_id, content_id)
        out += await _override_nodes(session, PlaybackOverride.layout_id, content_id)
    return out


async def reference_owners(
    session: AsyncSession, kind: Kind, content_id: UUID
) -> list[Node | None]:
    """Propriétaires des contenus que celui-ci référence."""
    out: list[Node | None] = []
    if kind == "playlist":
        rows = await session.execute(
            select(Media.owner_site_id, Media.owner_group_id)
            .join(PlaylistItemMedia, PlaylistItemMedia.media_id == Media.id)
            .join(PlaylistItem, PlaylistItem.id == PlaylistItemMedia.item_id)
            .where(PlaylistItem.playlist_id == content_id)
            .distinct()
        )
        out += [node_of_owner(s, g) for s, g in rows]
    elif kind == "layout":
        rows = await session.execute(
            select(Playlist.owner_site_id, Playlist.owner_group_id)
            .join(LayoutZone, LayoutZone.playlist_id == Playlist.id)
            .where(LayoutZone.layout_id == content_id)
            .distinct()
        )
        out += [node_of_owner(s, g) for s, g in rows]
        rows = await session.execute(
            select(Media.owner_site_id, Media.owner_group_id)
            .join(Layout, Layout.background_media_id == Media.id)
            .where(Layout.id == content_id)
        )
        out += [node_of_owner(s, g) for s, g in rows]
    return out


async def check_owner_change(
    session: AsyncSession, kind: Kind, content_id: UUID, new: Node | None
) -> None:
    """Changement de propriétaire : ses références doivent couvrir le nouveau nœud (422) et il doit
    couvrir tous ses référents (409, compteur seul)."""
    model = {"media": Media, "playlist": Playlist, "layout": Layout}[kind]
    # verrou exclusif sur le contenu : sérialise avec les créations de référence (FOR SHARE)
    await session.execute(
        select(model.id).where(model.id == content_id).with_for_update(of=model)  # type: ignore[attr-defined]
    )
    for owner in await reference_owners(session, kind, content_id):
        await check_reference(session, owner, new)
    narrower = 0
    for holder in await referent_nodes(session, kind, content_id):
        if not await covers(session, new, holder):
            narrower += 1
    if narrower:
        raise ApiError("scope.owner_narrower_than_references", 409, count=narrower)
