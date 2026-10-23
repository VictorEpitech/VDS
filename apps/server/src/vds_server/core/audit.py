"""Journal d'audit : une ligne ajoutée dans la transaction de l'action auditée."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import AuditLog


def record(
    session: AsyncSession,
    *,
    actor_kind: str,
    actor_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )
