"""Modèles `player_command` et `screenshot` (miroir de 0019) : commandes player et captures."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk
from vds_server.db.models import Timestamps


class PlayerCommand(Base, Timestamps):
    """Commande serveur -> player. Distincte de `remote_command` (file d'agent à `seq`)."""

    __tablename__ = "player_command"
    __table_args__ = (
        CheckConstraint(
            "name IN ('reload','restart','identify','screenshot','cache.resync')", name="name"
        ),
        CheckConstraint(
            "status IN ('queued','sent','ok','failed','expired','rejected','cancelled')",
            name="status",
        ),
        CheckConstraint("actor_kind IN ('user','module','system')", name="actor_kind"),
        Index(
            "uq_player_command_request_key",
            "actor_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
        ),
        Index(
            "ix_player_command_pending",
            "device_id",
            "status",
            postgresql_where=text("status IN ('queued','sent')"),
        ),
        Index("ix_player_command_screen_id", "screen_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()  # = cmd_id
    screen_id: Mapped[UUID | None] = mapped_column(ForeignKey("screen.id", ondelete="SET NULL"))
    device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("screen_device.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(Text)
    args: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    code: Mapped[str | None] = mapped_column(Text)
    request_key: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = tstz(now=False)
    sent_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    done_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    result: Mapped[Any | None] = mapped_column(JSONB)


class Screenshot(Base):
    __tablename__ = "screenshot"
    __table_args__ = (
        CheckConstraint("trigger IN ('on_demand','periodic')", name="trigger"),
        CheckConstraint("width > 0 AND height > 0 AND bytes > 0", name="size"),
        UniqueConstraint("storage_key"),
        Index("ix_screenshot_screen_id", "screen_id", text("captured_at DESC")),
        Index("ix_screenshot_received_at", "received_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    screen_id: Mapped[UUID] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    command_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("player_command.id", ondelete="SET NULL")
    )
    trigger: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = tstz(now=False)
    received_at: Mapped[datetime] = tstz()
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
