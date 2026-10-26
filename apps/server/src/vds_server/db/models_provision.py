"""Modèle `screen_provision` (miroir de 0005) : session de provisioning d'écran."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk

ACTIVE = ("queued", "running", "waiting_user", "waiting_online")
TERMINAL = ("done", "failed", "cancelled")


class ScreenProvision(Base):
    __tablename__ = "screen_provision"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','waiting_user','waiting_online','done','failed',"
            "'cancelled')",
            name="status",
        ),
        Index(
            "ux_screen_provision_active",
            "screen_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running','waiting_user','waiting_online')"),
        ),
        Index("ix_screen_provision_cmd_id", "cmd_id"),
        Index(
            "ux_screen_provision_ticket",
            "ticket_hash",
            unique=True,
            postgresql_where=text("ticket_hash IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    screen_id: Mapped[UUID] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("site_agent.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(INET)
    port: Mapped[int] = mapped_column(server_default="5555")
    status: Mapped[str] = mapped_column(Text)
    steps: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    outcome: Mapped[Any | None] = mapped_column(JSONB)
    code: Mapped[str | None] = mapped_column(Text)
    apk_version_code: Mapped[int | None] = mapped_column()
    ticket_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    ticket_expires_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    paired_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    online_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    cmd_id: Mapped[UUID | None] = mapped_column()
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = tstz()
    finished_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
