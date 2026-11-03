"""Modèle `content_feed` (miroir de 0020) : flux de données mis en cache (widgets)."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz


class ContentFeed(Base):
    __tablename__ = "content_feed"
    __table_args__ = (
        CheckConstraint("status IN ('ok','failing','never')", name="status"),
        CheckConstraint("refresh_s BETWEEN 300 AND 86400", name="refresh"),
        CheckConstraint("fail_count >= 0", name="fail_count"),
        Index("ix_content_feed_next_at", "next_at"),
        Index("ix_content_feed_module_id", "module_id"),
        Index("ix_content_feed_last_used_at", "last_used_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # feed_id
    module_id: Mapped[str] = mapped_column(Text)
    request: Mapped[Any] = mapped_column(JSONB)
    refresh_s: Mapped[int] = mapped_column(Integer)
    data: Mapped[Any | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    status: Mapped[str] = mapped_column(Text, server_default="never")
    error_code: Mapped[str | None] = mapped_column(Text)
    fail_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failing_since: Mapped[datetime | None] = tstz(nullable=True, now=False)
    next_at: Mapped[datetime] = tstz()
    last_used_at: Mapped[datetime] = tstz()
    created_at: Mapped[datetime] = tstz()
