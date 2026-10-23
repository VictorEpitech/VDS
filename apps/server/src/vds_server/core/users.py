"""Identité -> utilisateur : upsert de `user_account` par (provider, external_id)."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import AccessDenied
from vds_sdk.server import Identity

from vds_server.core import audit
from vds_server.db.models import UserAccount

Role = Literal["admin", "editor"]
DEFAULT_ROLE: Role = "editor"  # moindre privilège quand ni le provider ni VDS n'en fixent un
LAST_SEEN_STEP = timedelta(seconds=60)


async def upsert_identity(
    session: AsyncSession, identity: Identity, *, role_if_new: Role | None = None
) -> tuple[UserAccount, bool]:
    """Crée ou met à jour le compte. Le rôle du provider (s'il en donne un) fait foi ;
    sinon le rôle stocké dans VDS est conservé. Ne commit pas. Retourne (compte, créé)."""
    new_role = identity.role or role_if_new or DEFAULT_ROLE
    stmt = (
        insert(UserAccount)
        .values(
            auth_provider=identity.provider,
            external_id=identity.external_id,
            email=identity.email,
            display_name=identity.display_name,
            role=new_role,
        )
        .on_conflict_do_nothing(index_elements=["auth_provider", "external_id"])
        .returning(UserAccount.id)
    )
    created = (await session.execute(stmt)).scalar_one_or_none() is not None
    row = (
        await session.execute(
            select(UserAccount)
            .where(
                UserAccount.auth_provider == identity.provider,
                UserAccount.external_id == identity.external_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if created:
        audit.record(
            session,
            actor_kind="system",
            actor_id=None,
            action="user.created",
            target_type="user",
            target_id=str(row.id),
            details={"provider": identity.provider, "role": row.role},
        )
        return row, True
    if not row.is_active:
        raise AccessDenied("auth.account_inactive")
    if identity.role and row.role != identity.role:
        audit.record(
            session,
            actor_kind="system",
            actor_id=None,
            action="user.role_changed",
            target_type="user",
            target_id=str(row.id),
            details={"from": row.role, "to": identity.role},
        )
        row.role = identity.role
    if identity.email and row.email != identity.email:
        row.email = identity.email
    if row.display_name != identity.display_name:
        row.display_name = identity.display_name
    now = datetime.now(UTC)
    if row.last_seen_at is None or now - row.last_seen_at > LAST_SEEN_STEP:
        row.last_seen_at = now
    return row, False


async def get_user(session: AsyncSession, user_id: UUID) -> UserAccount | None:
    return await session.get(UserAccount, user_id)
