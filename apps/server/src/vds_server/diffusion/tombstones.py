"""File de suppression physique : écrite dans la transaction qui supprime la ligne en base."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import StorageTombstone


async def add_tombstones(
    session: AsyncSession,
    backend: str,
    keys: Iterable[str],
    *,
    reason: str,
    media_id: UUID | None,
    not_before: datetime | None = None,
) -> None:
    """Idempotent sur (backend, clé). Les clés viennent uniquement de lignes `media_variant`."""
    when = not_before or datetime.now(UTC)
    rows = [
        {"backend": backend, "key": key, "reason": reason, "media_id": media_id, "not_before": when}
        for key in dict.fromkeys(keys)
    ]
    if rows:
        await session.execute(insert(StorageTombstone).values(rows).on_conflict_do_nothing())
