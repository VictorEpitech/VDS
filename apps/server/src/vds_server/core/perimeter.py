"""Périmètre d'un principal (lot J) : nœuds organisation / site / groupe, jamais d'ACL par objet.

`admin` = toute l'organisation. `editor` = union des nœuds de `user_scope`. Résolu une fois par
requête dans `AuthPipeline.resolve` (pas de cache inter-requêtes : un retrait est effectif à la
requête suivante). Les acteurs module/système ne sont pas soumis au périmètre (J-D10).
"""

import hashlib
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Screen, ScreenGroup, UserScope

NodeType = Literal["organization", "site", "group"]
MAX_NODES = 100  # plafond applicatif de nœuds par utilisateur


@dataclass(frozen=True)
class Node:
    type: NodeType
    id: UUID


class NodeRef(BaseModel):
    type: NodeType
    id: UUID


@dataclass(frozen=True)
class Perimeter:
    unrestricted: bool  # admin, ou éditeur avec un nœud organisation
    sites: frozenset[UUID] = frozenset()  # sites attribués directement
    groups: frozenset[UUID] = frozenset()  # groupes attribués directement
    sites_closure: frozenset[UUID] = frozenset()  # sites ∪ sites parents des groupes (ancêtres)
    groups_closure: frozenset[UUID] = frozenset()  # groupes ∪ groupes des sites (descendants)
    version: str = "all"  # empreinte des nœuds, entre dans les ETag

    @property
    def empty(self) -> bool:
        return not self.unrestricted and not self.sites and not self.groups


UNRESTRICTED = Perimeter(unrestricted=True)
EMPTY = Perimeter(unrestricted=False, version="none")


def _version(sites: frozenset[UUID], groups: frozenset[UUID]) -> str:
    s, g = ",".join(sorted(map(str, sites))), ",".join(sorted(map(str, groups)))
    return hashlib.sha256(f"s:{s}|g:{g}".encode()).hexdigest()[:16]


async def load_perimeter(session: AsyncSession, user_id: UUID, role: str) -> Perimeter:
    """Une requête sur `user_scope` (+ une pour la fermeture si le périmètre est restreint)."""
    if role == "admin":
        return UNRESTRICTED
    rows = (
        await session.execute(
            select(UserScope.scope_type, UserScope.scope_id).where(UserScope.user_id == user_id)
        )
    ).all()
    if any(kind == "organization" for kind, _ in rows):
        return UNRESTRICTED
    sites = frozenset(i for kind, i in rows if kind == "site")
    groups = frozenset(i for kind, i in rows if kind == "group")
    if not sites and not groups:
        return EMPTY
    parents: set[UUID] = set()
    children: set[UUID] = set()
    q = select(ScreenGroup.id, ScreenGroup.site_id).where(
        ScreenGroup.id.in_(groups) | ScreenGroup.site_id.in_(sites)
    )
    for group_id, site_id in (await session.execute(q)).all():
        if group_id in groups:
            parents.add(site_id)
        if site_id in sites:
            children.add(group_id)
    return Perimeter(
        unrestricted=False,
        sites=sites,
        groups=groups,
        sites_closure=sites | parents,
        groups_closure=groups | children,
        version=_version(sites, groups),
    )


def node_of_owner(owner_site_id: UUID | None, owner_group_id: UUID | None) -> Node | None:
    """None = organisation (contenu partagé)."""
    if owner_group_id is not None:
        return Node("group", owner_group_id)
    if owner_site_id is not None:
        return Node("site", owner_site_id)
    return None


async def covers(session: AsyncSession, ancestor: Node | None, node: Node | None) -> bool:
    """J-D5 : `ancestor` est égal ou ancêtre de `node` (None = organisation, racine)."""
    if ancestor is None or ancestor.type == "organization":
        return True
    if node is None or node.type == "organization":
        return False
    if ancestor == node:
        return True
    if ancestor.type == "site" and node.type == "group":
        site_id = await session.scalar(select(ScreenGroup.site_id).where(ScreenGroup.id == node.id))
        return site_id == ancestor.id
    return False


async def node_of_target(session: AsyncSession, target_type: str, target_id: UUID) -> Node | None:
    """Nœud d'une cible de diffusion (J-D5) : organisation -> None ; écran -> groupe, sinon site."""
    if target_type == "site":
        return Node("site", target_id)
    if target_type == "group":
        return Node("group", target_id)
    if target_type == "screen":
        row = (
            await session.execute(
                select(Screen.site_id, Screen.group_id).where(Screen.id == target_id)
            )
        ).one_or_none()
        if row is not None:
            return Node("group", row[1]) if row[1] is not None else Node("site", row[0])
    return None


def owner_ref(owner_site_id: UUID | None, owner_group_id: UUID | None) -> NodeRef | None:
    node = node_of_owner(owner_site_id, owner_group_id)
    return None if node is None else NodeRef(type=node.type, id=node.id)
