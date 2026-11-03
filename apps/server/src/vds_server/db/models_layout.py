"""Modèles `layout` et `layout_zone` (miroir de 0017) : mises en page du noyau."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk
from vds_server.db.models import Timestamps


class Layout(Base, Timestamps):
    __tablename__ = "layout"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name"),
        CheckConstraint("orientation IN ('landscape','portrait')", name="orientation"),
        CheckConstraint(
            "background_color IS NULL OR background_color ~ '^#[0-9a-fA-F]{6}$'",
            name="background_color",
        ),
        CheckConstraint("cardinality(tags) <= 20", name="tags"),
        CheckConstraint("revision >= 1", name="revision"),
        UniqueConstraint("org_id", "name"),
        Index("ix_layout_tags", "tags", postgresql_using="gin"),
        Index("ix_layout_background_media_id", "background_media_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(Text)
    orientation: Mapped[str] = mapped_column(Text)
    background_color: Mapped[str | None] = mapped_column(Text)
    background_media_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL")
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class LayoutZone(Base):
    __tablename__ = "layout_zone"
    __table_args__ = (
        CheckConstraint("key ~ '^[a-z0-9_-]{1,32}$'", name="key"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="name"),
        CheckConstraint(
            "x >= 0 AND y >= 0 AND w > 0 AND h > 0 AND x + w <= 10000 AND y + h <= 10000",
            name="geometry",
        ),
        CheckConstraint("z BETWEEN 0 AND 99", name="z"),
        CheckConstraint("fit IN ('contain','cover','stretch')", name="fit"),
        CheckConstraint(
            "default_item_duration_ms IS NULL "
            "OR default_item_duration_ms BETWEEN 1000 AND 86400000",
            name="duration",
        ),
        CheckConstraint(
            "active_until IS NULL OR active_from IS NULL OR active_until > active_from",
            name="window",
        ),
        UniqueConstraint("layout_id", "key"),
        Index("ix_layout_zone_layout_id_z", "layout_id", "z"),
        Index("ix_layout_zone_playlist_id", "playlist_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    layout_id: Mapped[UUID] = mapped_column(ForeignKey("layout.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    x: Mapped[int] = mapped_column(Integer)  # dix-millièmes (0..10000)
    y: Mapped[int] = mapped_column(Integer)
    w: Mapped[int] = mapped_column(Integer)
    h: Mapped[int] = mapped_column(Integer)
    z: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    playlist_id: Mapped[UUID | None] = mapped_column(ForeignKey("playlist.id", ondelete="SET NULL"))
    fit: Mapped[str] = mapped_column(Text, server_default="contain")
    default_item_duration_ms: Mapped[int | None] = mapped_column(Integer)
    active_from: Mapped[datetime | None] = tstz(nullable=True, now=False)
    active_until: Mapped[datetime | None] = tstz(nullable=True, now=False)
