"""Modèle `agent_enrollment` (miroir de 0008) : code d'enrôlement d'agent, haché."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk


class AgentEnrollment(Base):
    __tablename__ = "agent_enrollment"
    __table_args__ = (
        UniqueConstraint("code_sha256"),
        CheckConstraint("channel IN ('remote','local')", name="channel"),
        CheckConstraint("char_length(consumed_hostname) <= 253", name="hostname"),
        CheckConstraint("consumed_at IS NULL OR revoked_at IS NULL", name="exclusive"),
        Index(
            "ix_agent_enrollment_pending",
            "site_id",
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[bytes] = mapped_column(LargeBinary)  # 32 octets, jamais le code
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = tstz()
    expires_at: Mapped[datetime] = tstz(now=False)
    consumed_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    consumed_ip: Mapped[str | None] = mapped_column(INET)
    consumed_hostname: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("site_agent.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
