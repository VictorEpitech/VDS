"""Service de publication : copie de travail -> instantané publié (docs/architecture-validation.md).

Deux usages :
- crochet `touch` appelé par les services d'écriture (playlist, mise en page, programmation) dans
  leur transaction : incrémente `working_rev` et publie automatiquement pour un admin (ou quand
  la validation est désactivée) ; sinon l'objet passe en `draft` ;
- transitions explicites (`submit`, `withdraw`, `approve`, `reject`) de l'API `/publications`.

Les événements sont rassemblés dans `events` ; l'appelant les publie après son commit.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Organization, Playlist, Schedule, UserAccount
from vds_server.db.models_layout import Layout
from vds_server.db.models_publication import Publication
from vds_server.publication import schemas as ps
from vds_server.publication import snapshots as snap

REVIEW_KEY = "diffusion.review_required"
Key = tuple[str, UUID]
_NOT_FOUND = {"playlist": "playlist.not_found", "layout": "layout.not_found"}


def _not_found(entity_type: str) -> ApiError:
    return ApiError(_NOT_FOUND.get(entity_type, "schedule.not_found"), 404)


def _refs(keys: list[Key]) -> list[dict[str, str]]:
    return [{"entity_type": t, "entity_id": str(i)} for t, i in keys]


def _event(name: str, payload: BaseModel) -> Event:
    return Event(name=name, payload=payload.model_dump(mode="json"), source="core")


class PublicationService:
    """`actor=None` : acteur système (forçages, reprise) - publie toujours, sans validation."""

    def __init__(
        self,
        session: AsyncSession,
        actor: Principal | None,
        schemas: ConfigSchemaProvider | None = None,
    ) -> None:
        self._s = session
        self._actor = actor
        self._schemas = schemas
        self._review: bool | None = None
        self.events: list[Event] = []

    def drain(self) -> list[Event]:
        events, self.events = self.events, []
        return events

    # --- configuration -----------------------------------------------------------------
    async def review_required(self) -> bool:
        if self._review is None:
            value = True
            org = await self._s.scalar(select(Organization.id))
            if self._schemas is not None and org is not None:
                res = await ConfigService(self._s, self._schemas).resolve(
                    "organization", org, CORE_NAMESPACE
                )
                found = res.values.get(REVIEW_KEY)
                value = True if found is None else bool(found.value)
            self._review = value
        return self._review

    def _is_admin(self) -> bool:
        return self._actor is None or self._actor.role == "admin"

    async def _auto_publish(self) -> bool:
        return self._is_admin() or not await self.review_required()

    def _actor_id(self) -> UUID | None:
        return None if self._actor is None else self._actor.user_id

    # --- lignes ------------------------------------------------------------------------
    async def _lock(
        self, entity_type: str, entity_id: UUID, *, create: bool = False
    ) -> Publication:
        if create:
            await self._s.execute(
                pg_insert(Publication)
                .values(entity_type=entity_type, entity_id=entity_id, state="draft", working_rev=0)
                .on_conflict_do_nothing()
            )
        row = (
            await self._s.execute(
                select(Publication)
                .where(Publication.entity_type == entity_type, Publication.entity_id == entity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise _not_found(entity_type)
        return row

    async def _lock_existing(self, entity_type: str, entity_id: UUID) -> Publication | None:
        return (
            await self._s.execute(
                select(Publication)
                .where(Publication.entity_type == entity_type, Publication.entity_id == entity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _exists(self, entity_type: str, entity_id: UUID) -> bool:
        model: Any = {"playlist": Playlist, "layout": Layout, "schedule": Schedule}[entity_type]
        return (await self._s.scalar(select(model.id).where(model.id == entity_id))) is not None

    async def _published(self, keys: list[Key]) -> set[Key]:
        found: set[Key] = set()
        for entity_type in {t for t, _ in keys}:
            ids = [i for t, i in keys if t == entity_type]
            rows = await self._s.scalars(
                select(Publication.entity_id).where(
                    Publication.entity_type == entity_type,
                    Publication.entity_id.in_(ids),
                    Publication.snapshot.is_not(None),
                )
            )
            found |= {(entity_type, i) for i in rows}
        return found

    async def _missing(self, built: snap.Snapshot) -> list[Key]:
        deps = snap.dependencies(built)
        have = await self._published(deps)
        return [d for d in deps if d not in have]

    async def missing_dependencies(self, entity_type: str, entity_id: UUID) -> list[Key]:
        """Dépendances de la copie de travail sans instantané publié."""
        built = await snap.build(self._s, entity_type, entity_id)
        return [] if built is None else await self._missing(built)

    # --- publication -------------------------------------------------------------------
    async def _publish(
        self,
        row: Publication,
        *,
        auto: bool,
        include: bool = False,
        author: UUID | None = None,
    ) -> list[Key]:
        """Écrit l'instantané de la copie de travail ; retourne les dépendances bloquantes."""
        built = await snap.build(self._s, row.entity_type, row.entity_id)
        if built is None:
            return []
        missing = await self._missing(built)
        if missing and include:
            for dep_type, dep_id in missing:
                dep = await self._lock_existing(dep_type, dep_id)
                # seules les dépendances soumises (in_review) et inchangées depuis la soumission
                # (expected_rev = submitted_rev) sont approuvées ; les autres restent bloquantes
                if dep is None or dep.state != "in_review" or dep.working_rev != dep.submitted_rev:
                    continue
                if await self._publish(dep, auto=False, include=True):
                    continue
                self._audit("publication.approved", dep, via="include_dependencies")
            missing = await self._missing(built)
        if missing:
            return missing
        now = datetime.now(UTC)
        author = author or row.submitted_by
        row.snapshot = snap.dump(built)
        row.published_rev, row.published_at = row.working_rev, now
        row.published_by = self._actor_id()
        row.state, row.updated_at = "published", now
        row.submitted_rev = row.submitted_by = row.submitted_at = None
        row.review_comment = None
        self.events.append(
            _event(
                "publication.approved",
                ps.PublicationApprovedV1(
                    entity_type=row.entity_type,  # type: ignore[arg-type]
                    entity_id=row.entity_id,
                    rev=row.working_rev,
                    actor_id=self._actor_id(),
                    author_id=author or self._actor_id(),
                    auto=auto,
                ),
            )
        )
        return []

    async def touch(self, entity_type: str, entity_id: UUID) -> ps.PublicationOut:
        """Crochet d'écriture : à appeler après le flush de la copie de travail."""
        await self._s.flush()
        row = await self._lock(entity_type, entity_id, create=True)
        row.working_rev += 1
        row.updated_at = datetime.now(UTC)
        submitter = row.submitted_by if row.state == "in_review" else None
        row.state = "draft"
        row.submitted_rev = row.submitted_by = row.submitted_at = None
        if await self._auto_publish():  # une révision en revue est alors réputée approuvée
            await self._publish(row, auto=True, author=submitter)
        await self._s.flush()
        return await self.out(row)

    async def touch_many(self, entity_type: str, ids: list[UUID]) -> None:
        for entity_id in ids:
            await self.touch(entity_type, entity_id)

    async def publish_system(self, entity_type: str, entity_id: UUID) -> None:
        """Publication sans validation (playlist éphémère d'un forçage, reprise)."""
        await self._s.flush()
        row = await self._lock(entity_type, entity_id, create=True)
        row.working_rev += 1
        await self._publish(row, auto=True)
        await self._s.flush()

    async def forget(self, entity_type: str, entity_id: UUID) -> None:
        await self._s.execute(
            Publication.__table__.delete().where(  # type: ignore[attr-defined]
                Publication.entity_type == entity_type, Publication.entity_id == entity_id
            )
        )

    async def ensure_deletable(self, entity_type: str, entity_id: UUID) -> None:
        """Un éditeur ne supprime pas un objet publié quand la validation est active."""
        if self._is_admin() or not await self.review_required():
            return
        published = await self._s.scalar(
            select(Publication.published_rev).where(
                Publication.entity_type == entity_type, Publication.entity_id == entity_id
            )
        )
        if published is not None:
            raise ApiError("publication.delete_requires_admin", 403)

    # --- sorties -----------------------------------------------------------------------
    async def _users(self, ids: set[UUID | None]) -> dict[UUID, ps.UserRef]:
        wanted = {i for i in ids if i is not None}
        if not wanted:
            return {}
        rows = await self._s.execute(
            select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(wanted))
        )
        return {i: ps.UserRef(id=i, display_name=n) for i, n in rows}

    async def _outs(
        self, rows: list[Publication], *, names: bool = False
    ) -> list[ps.PublicationOut]:
        users = await self._users({r.submitted_by for r in rows} | {r.published_by for r in rows})
        labels = await self._names(rows) if names else {}

        def ref(uid: UUID | None) -> ps.UserRef | None:
            return None if uid is None else users.get(uid, ps.UserRef(id=uid))

        return [
            ps.PublicationOut(
                entity_type=r.entity_type,  # type: ignore[arg-type]
                entity_id=r.entity_id,
                state=r.state,  # type: ignore[arg-type]
                working_rev=r.working_rev,
                published_rev=r.published_rev,
                submitted_by=ref(r.submitted_by),
                submitted_at=r.submitted_at,
                published_by=ref(r.published_by),
                published_at=r.published_at,
                review_comment=r.review_comment,
                name=labels.get((r.entity_type, r.entity_id)),
            )
            for r in rows
        ]

    async def _names(self, rows: list[Publication]) -> dict[Key, str]:
        out: dict[Key, str] = {}
        for entity_type, model in (
            ("playlist", Playlist),
            ("layout", Layout),
            ("schedule", Schedule),
        ):
            ids = [r.entity_id for r in rows if r.entity_type == entity_type]
            if ids:
                found = await self._s.execute(select(model.id, model.name).where(model.id.in_(ids)))
                out |= {(entity_type, i): n for i, n in found}
        return out

    async def list_outs(self, rows: list[Publication]) -> list[ps.PublicationOut]:
        return await self._outs(rows, names=True)

    async def out(self, row: Publication) -> ps.PublicationOut:
        """Un seul objet : `blocked_by` calculé (dépendances non publiées) hors état publié."""
        (out,) = await self._outs([row], names=True)
        if row.state != "published":
            missing = await self.missing_dependencies(row.entity_type, row.entity_id)
            out.blocked_by = [
                ps.EntityRef(entity_type=t, entity_id=i)  # type: ignore[arg-type]
                for t, i in await self._visible(missing)
            ]
        return out

    async def _visible(self, keys: list[Key]) -> list[Key]:
        """Un éditeur ne voit pas les identifiants hors de son périmètre (admin : tout)."""
        if self._is_admin() or self._actor is None:
            return keys
        from vds_server.diffusion.scope import PerimeterScope

        scope = PerimeterScope(self._s)
        out: list[Key] = []
        for entity_type, entity_id in keys:
            if entity_type == "playlist":
                model: Any = Playlist
                cond = scope.playlist_filter(self._actor)
            elif entity_type == "layout":
                model = Layout
                cond = scope.layout_filter(self._actor)
            else:
                continue
            if await self._s.scalar(select(model.id).where(model.id == entity_id, cond)):
                out.append((entity_type, entity_id))
        return out

    def _default(self, entity_type: str, entity_id: UUID) -> ps.PublicationOut:
        return ps.PublicationOut(
            entity_type=entity_type,  # type: ignore[arg-type]
            entity_id=entity_id,
            state="draft",
            working_rev=0,
            published_rev=None,
        )

    async def outs_for(self, entity_type: str, ids: list[UUID]) -> dict[UUID, ps.PublicationOut]:
        """Lot d'états (listes) ; un objet sans ligne (jamais écrit) est un brouillon vierge."""
        if not ids:
            return {}
        rows = list(
            (
                await self._s.scalars(
                    select(Publication).where(
                        Publication.entity_type == entity_type, Publication.entity_id.in_(ids)
                    )
                )
            ).all()
        )
        found = {o.entity_id: o for o in await self._outs(rows)}
        return {i: found.get(i) or self._default(entity_type, i) for i in ids}

    async def out_for(self, entity_type: str, entity_id: UUID) -> ps.PublicationOut:
        row = await self._s.scalar(
            select(Publication).where(
                Publication.entity_type == entity_type, Publication.entity_id == entity_id
            )
        )
        return self._default(entity_type, entity_id) if row is None else await self.out(row)

    async def attach(self, entity_type: str, outs: list[Any]) -> None:
        """Renseigne `.publication` des sorties (listes : sans `blocked_by`)."""
        states = await self.outs_for(entity_type, [o.id for o in outs])
        for o in outs:
            o.publication = states[o.id]

    async def attach_one(self, entity_type: str, out: Any) -> None:
        out.publication = await self.out_for(entity_type, out.id)

    # --- transitions explicites --------------------------------------------------------
    def _require_actor(self) -> Principal:
        if self._actor is None:  # jamais appelé hors API
            raise ApiError("auth.forbidden", 403)
        return self._actor

    def _audit(self, action: str, row: Publication, **details: Any) -> None:
        actor = self._require_actor()
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action=action,
            target_type=row.entity_type,
            target_id=str(row.entity_id),
            details={"rev": row.working_rev, **{k: v for k, v in details.items() if v}},
        )

    async def submit(self, entity_type: str, entity_id: UUID, comment: str | None) -> Publication:
        actor = self._require_actor()
        row = await self._lock(entity_type, entity_id)
        if row.state != "draft":
            raise ApiError("publication.invalid_transition", 409, state=row.state, action="submit")
        row.state, row.submitted_rev = "in_review", row.working_rev
        row.submitted_by, row.submitted_at = actor.user_id, datetime.now(UTC)
        row.updated_at, row.review_comment = row.submitted_at, None
        self._audit("publication.submitted", row, comment=comment)
        self.events.append(
            _event(
                "publication.submitted",
                ps.PublicationSubmittedV1(
                    entity_type=entity_type,  # type: ignore[arg-type]
                    entity_id=entity_id,
                    rev=row.working_rev,
                    actor_id=actor.user_id,
                    comment=comment,
                ),
            )
        )
        return row

    async def withdraw(self, entity_type: str, entity_id: UUID) -> Publication:
        actor = self._require_actor()
        row = await self._lock(entity_type, entity_id)
        if row.state != "in_review":
            raise ApiError(
                "publication.invalid_transition", 409, state=row.state, action="withdraw"
            )
        if actor.role != "admin" and row.submitted_by != actor.user_id:
            raise ApiError("auth.forbidden", 403, required="author")
        row.state, row.updated_at = "draft", datetime.now(UTC)
        row.submitted_rev = row.submitted_by = row.submitted_at = None
        self._audit("publication.withdrawn", row)
        self.events.append(
            _event(
                "publication.withdrawn",
                ps.PublicationWithdrawnV1(
                    entity_type=entity_type,  # type: ignore[arg-type]
                    entity_id=entity_id,
                    rev=row.working_rev,
                    actor_id=actor.user_id,
                ),
            )
        )
        return row

    async def approve(
        self, entity_type: str, entity_id: UUID, expected_rev: int, include_dependencies: bool
    ) -> Publication:
        self._require_actor()
        row = await self._lock(entity_type, entity_id)
        if row.state != "in_review":
            raise ApiError("publication.invalid_transition", 409, state=row.state, action="approve")
        if expected_rev != row.working_rev:
            raise ApiError("publication.stale", 409, working_rev=row.working_rev)
        missing = await self._publish(row, auto=False, include=include_dependencies)
        if missing:
            raise ApiError("publication.dependency_unpublished", 409, deps=_refs(missing))
        self._audit("publication.approved", row)
        return row

    async def reject(self, entity_type: str, entity_id: UUID, comment: str) -> Publication:
        actor = self._require_actor()
        row = await self._lock(entity_type, entity_id)
        if row.state != "in_review":
            raise ApiError("publication.invalid_transition", 409, state=row.state, action="reject")
        author = row.submitted_by
        row.state, row.updated_at, row.review_comment = "draft", datetime.now(UTC), comment
        row.submitted_rev = row.submitted_by = row.submitted_at = None
        self._audit("publication.rejected", row, comment=comment)
        self.events.append(
            _event(
                "publication.rejected",
                ps.PublicationRejectedV1(
                    entity_type=entity_type,  # type: ignore[arg-type]
                    entity_id=entity_id,
                    rev=row.working_rev,
                    actor_id=actor.user_id,
                    author_id=author,
                    comment=comment,
                ),
            )
        )
        return row
