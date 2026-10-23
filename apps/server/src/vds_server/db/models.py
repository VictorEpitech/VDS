"""Modèles du socle (miroir de la migration 0001_socle)."""

from datetime import date, datetime
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
    SmallInteger,
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


# --- Diffusion (miroir de 0002_diffusion) ------------------------------------


class ScreenDevice(Base, Timestamps):
    __tablename__ = "screen_device"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index(
            "uq_screen_device_active_screen",
            "screen_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    screen_id: Mapped[UUID] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(Text)
    paired_at: Mapped[datetime] = tstz()
    paired_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    last_seen_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    last_heartbeat: Mapped[Any | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    revoked_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    revoked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class PairingRequest(Base, Timestamps):
    __tablename__ = "pairing_request"
    __table_args__ = (
        CheckConstraint("status IN ('pending','claimed','consumed','expired')", name="status"),
        Index(
            "uq_pairing_request_pending_code",
            "code",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_pairing_request_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(CITEXT)
    poll_secret_hash: Mapped[str] = mapped_column(Text)
    device: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    screen_id: Mapped[UUID | None] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    claimed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = tstz(now=False)


class Media(Base, Timestamps):
    __tablename__ = "media"
    __table_args__ = (
        CheckConstraint("kind IN ('image','video','document')", name="kind"),
        CheckConstraint("status IN ('processing','ready','failed')", name="status"),
        Index("ix_media_sha256", "sha256"),
        Index("ix_media_kind", "kind"),
    )

    id: Mapped[UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    sha256: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    storage_backend: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="processing")
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class MediaVariant(Base, Timestamps):
    __tablename__ = "media_variant"

    media_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    mime: Mapped[str] = mapped_column(Text)
    bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    sha256: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)


class Playlist(Base, Timestamps):
    __tablename__ = "playlist"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class PlaylistItem(Base, Timestamps):
    __tablename__ = "playlist_item"
    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms > 0", name="duration"),
        UniqueConstraint("playlist_id", "position", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[UUID] = uuid_pk()
    playlist_id: Mapped[UUID] = mapped_column(ForeignKey("playlist.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(Text)
    props: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))


class PlaylistItemMedia(Base):
    __tablename__ = "playlist_item_media"
    __table_args__ = (Index("ix_playlist_item_media_media_id", "media_id"),)

    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("playlist_item.id", ondelete="CASCADE"), primary_key=True
    )
    media_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"), primary_key=True
    )


class Schedule(Base, Timestamps):
    __tablename__ = "schedule"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('organization','site','group','screen')", name="target_type"
        ),
        CheckConstraint(
            "starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on", name="period"
        ),
        Index("ix_schedule_target", "target_type", "target_id"),
        Index("ix_schedule_playlist_id", "playlist_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    playlist_id: Mapped[UUID] = mapped_column(ForeignKey("playlist.id", ondelete="RESTRICT"))
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID] = mapped_column()
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    starts_on: Mapped[date | None] = mapped_column()
    ends_on: Mapped[date | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    conditions: Mapped[Any | None] = mapped_column(JSONB)  # point d'accroche, ignoré en V1
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class ScheduleRule(Base, Timestamps):
    __tablename__ = "schedule_rule"
    __table_args__ = (
        CheckConstraint("weekdays BETWEEN 1 AND 127", name="weekdays"),
        CheckConstraint(
            "start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440", name="range"
        ),
        Index("ix_schedule_rule_schedule_id", "schedule_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    schedule_id: Mapped[UUID] = mapped_column(ForeignKey("schedule.id", ondelete="CASCADE"))
    weekdays: Mapped[int] = mapped_column(SmallInteger)
    start_minute: Mapped[int] = mapped_column(SmallInteger)
    end_minute: Mapped[int] = mapped_column(SmallInteger)
