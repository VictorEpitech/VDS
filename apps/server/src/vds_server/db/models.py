"""Modèles du socle (miroir de la migration 0001_socle)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk


class Timestamps:
    created_at: Mapped[datetime] = tstz()
    updated_at: Mapped[datetime] = tstz()


class Organization(Base, Timestamps):
    __tablename__ = "organization"
    __table_args__ = (CheckConstraint("singleton", name="singleton"),)

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    singleton: Mapped[bool] = mapped_column(server_default=text("true"), unique=True)


class Site(Base, Timestamps):
    __tablename__ = "site"

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    archived_at: Mapped[datetime | None] = tstz(nullable=True, now=False)


class ScreenGroup(Base, Timestamps):
    __tablename__ = "screen_group"
    __table_args__ = (
        UniqueConstraint("site_id", "name"),
        UniqueConstraint("id", "site_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)


class Screen(Base, Timestamps):
    __tablename__ = "screen"
    __table_args__ = (
        ForeignKeyConstraint(["group_id", "site_id"], ["screen_group.id", "screen_group.site_id"]),
        CheckConstraint("orientation IN ('landscape','portrait')", name="orientation"),
        Index("ix_screen_site_id", "site_id"),
        Index("ix_screen_group_id", "group_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"))
    group_id: Mapped[UUID | None] = mapped_column()
    name: Mapped[str] = mapped_column(Text)
    orientation: Mapped[str] = mapped_column(Text, server_default="landscape")
    archived_at: Mapped[datetime | None] = tstz(nullable=True, now=False)


class UserAccount(Base, Timestamps):
    __tablename__ = "user_account"
    __table_args__ = (
        UniqueConstraint("auth_provider", "external_id"),
        CheckConstraint("role IN ('admin','editor')", name="role"),
        Index("ix_user_account_email", "email"),
    )

    id: Mapped[UUID] = uuid_pk()
    auth_provider: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(CITEXT)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    last_seen_at: Mapped[datetime | None] = tstz(nullable=True, now=False)


class ConfigOverride(Base, Timestamps):
    __tablename__ = "config_override"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(organization_id, site_id, group_id, screen_id) = 1", name="one_scope"
        ),
        UniqueConstraint("scope_id", "namespace", "key"),
        Index("ix_config_override_namespace_key", "namespace", "key"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE")
    )
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    group_id: Mapped[UUID | None] = mapped_column(ForeignKey("screen_group.id", ondelete="CASCADE"))
    screen_id: Mapped[UUID | None] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    scope_level: Mapped[int] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN screen_id IS NOT NULL THEN 3 WHEN group_id IS NOT NULL THEN 2 "
            "WHEN site_id IS NOT NULL THEN 1 ELSE 0 END",
            persisted=True,
        ),
    )
    scope_id: Mapped[UUID] = mapped_column(
        Computed("COALESCE(screen_id, group_id, site_id, organization_id)", persisted=True)
    )
    namespace: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class Module(Base, Timestamps):
    __tablename__ = "module"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('player-adapter','power-driver','content-type','trigger',"
            "'notifier','auth-provider','theme','storage')",
            name="kind",
        ),
        CheckConstraint("source IN ('builtin','installed')", name="source"),
        CheckConstraint(
            "state IN ('discovered','enabled','disabled','quarantined','incompatible')",
            name="state",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="discovered")
    manifest: Mapped[Any] = mapped_column(JSONB)
    config_schema: Mapped[Any | None] = mapped_column(JSONB)
    checksum: Mapped[str | None] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    state_changed_at: Mapped[datetime] = tstz()


class EventOutbox(Base, Timestamps):
    __tablename__ = "event_outbox"
    __table_args__ = (Index("ix_event_outbox_occurred_at", "occurred_at"),)

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    source: Mapped[str] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = tstz()


class EventDelivery(Base, Timestamps):
    __tablename__ = "event_delivery"
    __table_args__ = (
        CheckConstraint("status IN ('pending','done','failed')", name="status"),
        Index(
            "ix_event_delivery_next_attempt_at",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_outbox.id", ondelete="CASCADE"), primary_key=True
    )
    subscriber: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime | None] = tstz(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base, Timestamps):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("actor_kind IN ('user','module','system','device')", name="actor_kind"),
        Index("ix_audit_log_target", "target_type", "target_id"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = tstz()
    actor_kind: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
