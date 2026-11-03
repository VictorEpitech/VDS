"""Modèle `playback_overrides` (miroir de 0015) : forçage de diffusion avec fin automatique.

Nommé `PlaybackOverride` pour ne pas être confondu avec `ConfigOverride` (surcharges de config).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk
from vds_server.db.models import Timestamps


class PlaybackOverride(Base, Timestamps):
    __tablename__ = "playback_overrides"
    __table_args__ = (
        CheckConstraint("kind IN ('playlist','off')", name="kind"),
        CheckConstraint(
            "target_type IN ('organization','site','group','screen')", name="target_type"
        ),
        CheckConstraint("ends_at > starts_at", name="period"),
        CheckConstraint("priority BETWEEN -1000 AND 1000", name="priority"),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('stopped','expired')", name="end_reason"
        ),
        CheckConstraint(
            "(kind = 'off') = (playlist_id IS NULL AND layout_id IS NULL)", name="playlist"
        ),
        CheckConstraint("playlist_id IS NULL OR layout_id IS NULL", name="content_target"),
        CheckConstraint(
            "zone_ids IS NULL OR cardinality(zone_ids) BETWEEN 1 AND 32", name="zone_count"
        ),
        CheckConstraint(
            "zone_ids IS NULL OR (playlist_id IS NOT NULL AND layout_id IS NULL)",
            name="zone_playlist",
        ),
        CheckConstraint("zone_ids IS NULL OR kind = 'playlist'", name="zone_kind"),
        Index("ix_playback_overrides_layout_id", "layout_id"),
        Index("ix_playback_overrides_target", "target_type", "target_id"),
        Index(
            "ix_playback_overrides_ends_at",
            "ends_at",
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "uq_playback_overrides_request_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    playlist_id: Mapped[UUID | None] = mapped_column(ForeignKey("playlist.id"))
    layout_id: Mapped[UUID | None] = mapped_column(ForeignKey("layout.id", ondelete="RESTRICT"))
    zone_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # NULL = forçage d'écran
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[UUID] = mapped_column()
    starts_at: Mapped[datetime] = tstz(now=False)
    ends_at: Mapped[datetime] = tstz(now=False)
    ended_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    end_reason: Mapped[str | None] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    respect_scheduled_off: Mapped[bool | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_by_module: Mapped[str | None] = mapped_column(String(120))
    ended_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    request_id: Mapped[UUID | None] = mapped_column()
