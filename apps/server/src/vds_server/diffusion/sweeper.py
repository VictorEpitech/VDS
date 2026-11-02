"""Balayeurs de la médiathèque : expiration, purge différée, suppression physique des fichiers.

- `expire` (60 s) : constate l'expiration (`expired_at`), audit système, événement.
- `purge` (1 h) : après `core.content.expired_retention_days`, supprime base et fichiers.
- `gc` (5 min) : exécute la file `storage_tombstone` auprès du backend enregistré par ligne.

Chaque passe prend un verrou consultatif PostgreSQL (plusieurs workers) et reste idempotente.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.server.storage import StorageBackend

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core import audit
from vds_server.db.models import (
    Media,
    MediaVariant,
    Organization,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    StorageTombstone,
)
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion.tombstones import add_tombstones

log = logging.getLogger("vds.content")

BATCH = 100
EXPIRE_EVERY_S = 60.0
GC_EVERY_S = 300.0
PURGE_EVERY_S = 3600.0
MAX_ATTEMPTS = 10
MAX_BACKOFF = timedelta(hours=6)
DEFAULT_RETENTION_DAYS = 30
BackendLookup = Callable[[str], StorageBackend | None]


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _system(
    session: AsyncSession, action: str, target_type: str, target_id: UUID, **details: Any
) -> None:
    audit.record(
        session,
        actor_kind="system",
        actor_id=None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details,
    )


class ContentSweeper:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        schemas: ConfigSchemaProvider,
        backend_of: BackendLookup,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._schemas = schemas
        self._backend_of = backend_of
        self._last: dict[str, float] = {}

    async def _lock(self, s: AsyncSession, name: str) -> bool:
        got = await s.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": name}
        )
        return bool(got)

    async def _publish(self, events: list[Event]) -> None:
        for event in events:
            await self._bus.publish(event)

    # --- expiration ---------------------------------------------------------------------
    async def expire(self, now: datetime | None = None) -> list[Event]:
        """Constate les expirations échues. Retourne les événements publiés."""
        now = now or datetime.now(UTC)
        events: list[Event] = []
        async with self._maker() as s:
            if not await self._lock(s, "content.expire"):
                return []
            medias = (
                await s.execute(
                    select(Media)
                    .where(
                        Media.expires_at.is_not(None),
                        Media.expires_at <= now,
                        Media.expired_at.is_(None),
                    )
                    .order_by(Media.expires_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for m in medias:
                assert m.expires_at is not None  # noqa: S101 - filtré ci-dessus
                m.expired_at = now
                _system(s, "media.expired", "media", m.id, expires_at=_iso(m.expires_at))
                events.append(
                    Event(
                        name="media.expired",
                        payload={"media_id": str(m.id), "expires_at": _iso(m.expires_at)},
                        source="core",
                    )
                )
            playlists = (
                await s.execute(
                    select(Playlist)
                    .where(
                        Playlist.expires_at.is_not(None),
                        Playlist.expires_at <= now,
                        Playlist.expired_at.is_(None),
                    )
                    .order_by(Playlist.expires_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for p in playlists:
                assert p.expires_at is not None  # noqa: S101
                p.expired_at = now
                _system(s, "playlist.expired", "playlist", p.id, expires_at=_iso(p.expires_at))
                events.append(
                    Event(
                        name="playlist.expired",
                        payload={"playlist_id": str(p.id), "expires_at": _iso(p.expires_at)},
                        source="core",
                    )
                )
            await s.commit()
        await self._publish(events)
        return events

    # --- purge --------------------------------------------------------------------------
    async def retention_days(self, s: AsyncSession) -> int:
        org = await s.scalar(select(Organization.id))
        if org is None:
            return DEFAULT_RETENTION_DAYS
        res = await ConfigService(s, self._schemas).resolve("organization", org, CORE_NAMESPACE)
        return int(res.values["content.expired_retention_days"].value)

    async def purge(self, now: datetime | None = None) -> list[Event]:
        """Supprime médias puis playlists expirés depuis plus de la rétention. Audit conservé."""
        now = now or datetime.now(UTC)
        events: list[Event] = []
        async with self._maker() as s:
            if not await self._lock(s, "content.purge"):
                return []
            cutoff = now - timedelta(days=await self.retention_days(s))
            events += await self._purge_media(s, cutoff)
            events += await self._purge_playlists(s, cutoff)
            await s.commit()
        await self._publish(events)
        return events

    async def _purge_media(self, s: AsyncSession, cutoff: datetime) -> list[Event]:
        events: list[Event] = []
        medias = (
            await s.execute(
                select(Media)
                .where(Media.expired_at.is_not(None), Media.expired_at <= cutoff)
                .order_by(Media.expired_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        touched: set[UUID] = set()
        for media in list(medias):
            refs = (
                await s.execute(
                    select(PlaylistItem.id, PlaylistItem.playlist_id)
                    .join(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
                    .where(PlaylistItemMedia.media_id == media.id)
                )
            ).all()
            keys = set(
                (
                    await s.scalars(
                        select(MediaVariant.storage_key).where(MediaVariant.media_id == media.id)
                    )
                ).all()
            )
            if media.storage_key:
                keys.add(media.storage_key)
            if refs:
                await s.execute(
                    delete(PlaylistItem).where(PlaylistItem.id.in_([r[0] for r in refs]))
                )
            playlist_ids = sorted({r[1] for r in refs}, key=str)
            removed = [{"playlist_id": str(pid), "item_id": str(iid)} for iid, pid in refs]
            await add_tombstones(
                s,
                media.storage_backend or "storage.local",
                keys,
                reason="purged",
                media_id=media.id,
            )
            _system(
                s,
                "media.purged",
                "media",
                media.id,
                title=media.title,
                sha256=media.sha256,
                bytes=media.bytes,
                removed_items=removed,
            )
            await s.delete(media)
            await s.flush()
            events.append(
                Event(
                    name="media.purged",
                    payload={
                        "media_id": str(media.id),
                        "playlist_ids": [str(p) for p in playlist_ids],
                    },
                    source="core",
                )
            )
            touched.update(playlist_ids)
        for pid in sorted(touched, key=str):
            await s.execute(
                text("UPDATE playlist SET updated_at = now() WHERE id = :p"), {"p": pid}
            )
            events.append(
                Event(
                    name="playlist.updated",
                    payload={"playlist_id": str(pid), "change": "media_purged"},
                    source="core",
                )
            )
        return events

    async def _purge_playlists(self, s: AsyncSession, cutoff: datetime) -> list[Event]:
        events: list[Event] = []
        referenced = select(PlaybackOverride.playlist_id).where(
            PlaybackOverride.playlist_id.is_not(None)
        )
        playlists = (
            await s.execute(
                select(Playlist)
                .where(
                    Playlist.expired_at.is_not(None),
                    Playlist.expired_at <= cutoff,
                    Playlist.id.not_in(referenced),  # les forçages sont purgés d'abord
                )
                .order_by(Playlist.expired_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        for playlist in list(playlists):
            schedule_ids = list(
                (
                    await s.scalars(select(Schedule.id).where(Schedule.playlist_id == playlist.id))
                ).all()
            )
            if schedule_ids:
                await s.execute(delete(Schedule).where(Schedule.id.in_(schedule_ids)))
            _system(
                s,
                "playlist.purged",
                "playlist",
                playlist.id,
                name=playlist.name,
                removed_schedules=[str(i) for i in schedule_ids],
            )
            await s.delete(playlist)
            await s.flush()
            for sid in schedule_ids:
                events.append(
                    Event(
                        name="schedule.deleted",
                        payload={"schedule_id": str(sid), "reason": "playlist_purged"},
                        source="core",
                    )
                )
            events.append(
                Event(
                    name="playlist.purged",
                    payload={
                        "playlist_id": str(playlist.id),
                        "schedule_ids": [str(i) for i in schedule_ids],
                    },
                    source="core",
                )
            )
        return events

    # --- suppression physique -----------------------------------------------------------
    async def gc(self, now: datetime | None = None) -> int:
        """Exécute les tombstones échues. Retourne le nombre de fichiers supprimés."""
        now = now or datetime.now(UTC)
        done = 0
        async with self._maker() as s:
            if not await self._lock(s, "storage.gc"):
                return 0
            rows = (
                await s.execute(
                    select(StorageTombstone)
                    .where(
                        StorageTombstone.not_before <= now, StorageTombstone.attempts < MAX_ATTEMPTS
                    )
                    .order_by(StorageTombstone.not_before)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for row in list(rows):
                backend = self._backend_of(row.backend)
                if backend is None:  # module désactivé : la ligne attend
                    continue
                try:
                    await backend.delete(row.key)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    row.not_before = now + min(MAX_BACKOFF, timedelta(minutes=2**row.attempts))
                    log.warning(
                        "storage.gc failed backend=%s attempts=%d", row.backend, row.attempts
                    )
                    if row.attempts >= MAX_ATTEMPTS:
                        log.error("storage.delete_failed backend=%s key=%s", row.backend, row.key)
                        audit.record(
                            s,
                            actor_kind="system",
                            actor_id=None,
                            action="storage.delete_failed",
                            target_type="media" if row.media_id else None,
                            target_id=str(row.media_id) if row.media_id else None,
                            details={"backend": row.backend, "key": row.key, "reason": row.reason},
                        )
                    continue
                await s.delete(row)
                done += 1
            await s.commit()
        return done

    # --- boucle -------------------------------------------------------------------------
    def _due(self, name: str, every: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(name, float("-inf")) < every:
            return False
        self._last[name] = now
        return True

    async def run_once(self) -> None:
        """Appelé toutes les ~30 s par le runtime ; chaque tâche suit sa propre cadence."""
        for name, every, job in (
            ("expire", EXPIRE_EVERY_S, self.expire),
            ("purge", PURGE_EVERY_S, self.purge),
            ("gc", GC_EVERY_S, self.gc),
        ):
            if self._due(name, every):
                try:
                    await job()
                except Exception:
                    log.exception("content.%s_failed", name)
