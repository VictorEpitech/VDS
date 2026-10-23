"""Modèles lot 3 : agent de site, commandes, alimentation, incidents (miroir de 0003)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk
from vds_server.db.models import Timestamps


def _user_fk() -> Mapped[UUID | None]:
    return mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))


class SiteAgent(Base, Timestamps):
    __tablename__ = "site_agent"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index(
            "uq_site_agent_active_site",
            "site_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    token_prefix: Mapped[str] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    last_heartbeat: Mapped[Any | None] = mapped_column(JSONB)
    last_hello: Mapped[Any | None] = mapped_column(JSONB)
    enrolled_by: Mapped[UUID | None] = _user_fk()
    rotated_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    revoked_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    revoked_by: Mapped[UUID | None] = _user_fk()


class RemoteCommand(Base):
    __tablename__ = "remote_command"
    __table_args__ = (
        CheckConstraint("target_kind IN ('agent','device')", name="target_kind"),
        CheckConstraint(
            "status IN ('queued','sent','ok','failed','expired','rejected')", name="status"
        ),
        UniqueConstraint("target_kind", "target_id", "seq"),
        Index(
            "ix_remote_command_pending",
            "target_kind",
            "target_id",
            "seq",
            postgresql_where=text("status IN ('queued','sent')"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID] = mapped_column()
    seq: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text)
    screen_id: Mapped[UUID | None] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    args: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    code: Mapped[str | None] = mapped_column(Text)
    result: Mapped[Any | None] = mapped_column(JSONB)
    correlation_id: Mapped[UUID | None] = mapped_column()
    created_by: Mapped[UUID | None] = _user_fk()
    created_at: Mapped[datetime] = tstz()
    sent_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    done_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    expires_at: Mapped[datetime] = tstz(now=False)


class PowerPlan(Base, Timestamps):
    __tablename__ = "power_plan"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = _user_fk()
    updated_by: Mapped[UUID | None] = _user_fk()


class PowerPlanRule(Base, Timestamps):
    __tablename__ = "power_plan_rule"
    __table_args__ = (
        CheckConstraint("weekdays BETWEEN 1 AND 127", name="weekdays"),
        CheckConstraint(
            "start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440", name="range"
        ),
        Index("ix_power_plan_rule_plan_id", "plan_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("power_plan.id", ondelete="CASCADE"))
    weekdays: Mapped[int] = mapped_column(SmallInteger)
    start_minute: Mapped[int] = mapped_column(SmallInteger)
    end_minute: Mapped[int] = mapped_column(SmallInteger)


class PowerException(Base, Timestamps):
    __tablename__ = "power_exception"
    __table_args__ = (
        CheckConstraint(
            "scope_level IN ('organization','site','group','screen')", name="scope_level"
        ),
        CheckConstraint("starts_on <= ends_on", name="period"),
        CheckConstraint("state IN ('on','off')", name="state"),
        Index("ix_power_exception_scope", "scope_level", "scope_id", "starts_on", "ends_on"),
    )

    id: Mapped[UUID] = uuid_pk()
    scope_level: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[UUID | None] = mapped_column()
    starts_on: Mapped[date] = mapped_column()
    ends_on: Mapped[date] = mapped_column()
    start_minute: Mapped[int | None] = mapped_column(SmallInteger)
    end_minute: Mapped[int | None] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID | None] = _user_fk()
    updated_by: Mapped[UUID | None] = _user_fk()


class PowerCalendar(Base, Timestamps):
    __tablename__ = "power_calendar"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(Text)
    module_id: Mapped[str] = mapped_column(Text)
    name: Mapped[Any] = mapped_column(JSONB)
    refreshed_at: Mapped[datetime | None] = tstz(nullable=True, now=False)


class PowerCalendarDay(Base):
    __tablename__ = "power_calendar_day"

    calendar_id: Mapped[UUID] = mapped_column(
        ForeignKey("power_calendar.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text)


class PowerState(Base):
    __tablename__ = "power_state"
    __table_args__ = (
        CheckConstraint("desired IN ('on','off')", name="desired"),
        CheckConstraint("observed IN ('on','off','standby','unknown')", name="observed"),
    )

    screen_id: Mapped[UUID] = mapped_column(
        ForeignKey("screen.id", ondelete="CASCADE"), primary_key=True
    )
    desired: Mapped[str] = mapped_column(Text)
    desired_reason: Mapped[str] = mapped_column(Text)
    desired_since: Mapped[datetime] = tstz(now=False)
    observed: Mapped[str] = mapped_column(Text, server_default="unknown")
    observed_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    driver: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(server_default=text("false"))
    last_code: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = tstz()


class PowerAction(Base):
    __tablename__ = "power_action"
    __table_args__ = (Index("ix_power_action_screen_at", "screen_id", text("at DESC")),)

    id: Mapped[UUID] = uuid_pk()
    screen_id: Mapped[UUID] = mapped_column(ForeignKey("screen.id", ondelete="CASCADE"))
    request_id: Mapped[UUID | None] = mapped_column()
    state: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    driver: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column()
    verified: Mapped[bool] = mapped_column()
    code: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = tstz(now=False)


class Incident(Base):
    __tablename__ = "incident"
    __table_args__ = (
        CheckConstraint("severity IN ('info','warning','critical')", name="severity"),
        CheckConstraint("subject_type IN ('screen','agent','module','site')", name="subject_type"),
        Index(
            "uq_incident_open",
            "kind",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    subject_type: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[str] = mapped_column(Text)
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    details: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    opened_at: Mapped[datetime] = tstz(now=False)
    resolved_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
