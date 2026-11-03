"""Modèle `publication` (miroir de 0021) : état de validation et instantané publié.

Les tables playlist / layout / schedule restent la copie de travail ; `snapshot` est l'unique
source lue par le résolveur de programmation (docs/architecture-validation.md).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz


class Publication(Base):
    __tablename__ = "publication"
    __table_args__ = (
        CheckConstraint("entity_type IN ('playlist','layout','schedule')", name="entity_type"),
        CheckConstraint("state IN ('draft','in_review','published')", name="state"),
        CheckConstraint("working_rev >= 0", name="working_rev"),
        CheckConstraint(
            "review_comment IS NULL OR char_length(review_comment) <= 500", name="comment"
        ),
        Index(
            "ix_publication_in_review", "updated_at", postgresql_where=text("state = 'in_review'")
        ),
        Index(
            "ix_publication_schedule_target",
            text("(snapshot->>'target_id')"),
            postgresql_where=text("entity_type = 'schedule'"),
        ),
    )

    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(Text, server_default="draft")
    working_rev: Mapped[int] = mapped_column(Integer, server_default="1")
    submitted_rev: Mapped[int | None] = mapped_column(Integer)
    submitted_by: Mapped[UUID | None] = mapped_column()
    submitted_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    published_rev: Mapped[int | None] = mapped_column(Integer)
    published_by: Mapped[UUID | None] = mapped_column()
    published_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    snapshot: Mapped[Any | None] = mapped_column(JSONB)
    review_comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = tstz()
