"""Commande d'exploitation : (re)génère une variante (miniature) des médias existants.

    python -m vds_server.media_reprocess --variant thumb [--force] [--limit N] [--dry-run]

Relance `process_media` du module content-type actif sans changer la révision, l'original ni les
autres variantes (leurs écritures sont ignorées) ; seule la variante demandée est enregistrée.
"""

import argparse
import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from vds_sdk.server.content import ContentType, MediaMeta, MediaRef
from vds_sdk.server.storage import StorageBackend, StoredObject

from vds_server.db.models import Media, MediaVariant
from vds_server.diffusion.media_files import _VariantStore
from vds_server.diffusion.tombstones import add_tombstones

log = logging.getLogger("vds.media_reprocess")
GRACE = timedelta(hours=48)


@dataclass
class Report:
    scanned: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0


class _OnlyVariantStore(_VariantStore):
    """Ne conserve que la variante demandée : les autres écritures du module sont drainées."""

    def __init__(self, storage: StorageBackend, key: str, tag: str, only: str) -> None:
        super().__init__(storage, key, tag)
        self._only = only

    async def put_variant(
        self, name: str, chunks: AsyncIterator[bytes], *, mime: str
    ) -> StoredObject:
        if name != self._only:
            async for _ in chunks:
                pass
            return StoredObject(key="", bytes=0, sha256="", mime=mime)
        return await super().put_variant(name, chunks, mime=mime)


async def reprocess(
    rt: Any,
    variant: str,
    *,
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> Report:
    """`rt` : Runtime démarré (registre actif, stockage disponible)."""
    report = Report()
    storage: StorageBackend | None = rt.storage_backend()
    if storage is None:
        raise RuntimeError("media.storage_unavailable")
    backend_id = getattr(storage, "module_id", None) or "storage.local"
    async with rt.maker() as s:
        ids = list(
            (
                await s.scalars(
                    select(Media.id).where(Media.status == "ready").order_by(Media.created_at)
                )
            ).all()
        )
    for media_id in ids:
        if limit is not None and report.created >= limit:
            break
        async with rt.maker() as s:
            media = (
                await s.execute(select(Media).where(Media.id == media_id).with_for_update())
            ).scalar_one_or_none()
            if media is None or not media.storage_key:
                continue
            report.scanned += 1
            current = (
                await s.execute(
                    select(MediaVariant).where(
                        MediaVariant.media_id == media_id, MediaVariant.name == variant
                    )
                )
            ).scalar_one_or_none()
            found = rt.registry.content_processor(media.mime)
            if (current is not None and not force) or found is None:
                report.skipped += 1
                continue
            instance = found[1]
            if not isinstance(instance, ContentType) or dry_run:
                report.skipped += 1
                continue
            store = _OnlyVariantStore(
                storage, media.storage_key, f"r{media.revision}-{secrets.token_hex(4)}", variant
            )
            ref = MediaRef(
                id=media.id,
                kind=media.kind,
                mime=media.mime,
                bytes=media.bytes,
                sha256=media.sha256 or "",
                storage_key=media.storage_key,
            )
            meta: MediaMeta
            try:
                meta = await instance.process_media(ref, store)
            except Exception as exc:
                log.warning("reprocess failed media=%s: %s", media_id, type(exc).__name__)
                report.failed += 1
                await add_tombstones(
                    s,
                    media.storage_backend or backend_id,
                    [v.key for v in store.created.values() if v.key],
                    reason="aborted",
                    media_id=media_id,
                )
                await s.commit()
                continue
            del meta  # métadonnées inchangées : on ne touche pas à la ligne `media`
            obj = store.created.get(variant)
            if obj is None:
                report.skipped += 1
                continue
            if current is None:
                s.add(
                    MediaVariant(
                        media_id=media_id,
                        name=variant,
                        mime=obj.mime,
                        bytes=obj.bytes,
                        sha256=obj.sha256,
                        storage_key=obj.key,
                    )
                )
            else:
                if current.storage_key != obj.key:
                    await add_tombstones(
                        s,
                        media.storage_backend or backend_id,
                        [current.storage_key],
                        reason="replaced",
                        media_id=media_id,
                        not_before=datetime.now(UTC) + GRACE,
                    )
                current.mime, current.bytes = obj.mime, obj.bytes
                current.sha256, current.storage_key = obj.sha256, obj.key
            await s.commit()
            report.created += 1
    return report


async def _main(args: argparse.Namespace) -> Report:
    from vds_server.core.runtime import Runtime
    from vds_server.settings import Settings

    rt = Runtime(Settings())
    await rt.ensure_organization()
    await rt.registry.start()
    try:
        return await reprocess(
            rt, args.variant, force=args.force, limit=args.limit, dry_run=args.dry_run
        )
    finally:
        await rt.registry.stop()
        await rt.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vds_server.media_reprocess")
    parser.add_argument("--variant", default="thumb", help="nom de la variante à (re)générer")
    parser.add_argument("--force", action="store_true", help="régénère même si elle existe")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    report = asyncio.run(_main(parser.parse_args()))
    print(json.dumps(asdict(report)))  # noqa: T201 - sortie de commande d'exploitation


if __name__ == "__main__":
    main()
