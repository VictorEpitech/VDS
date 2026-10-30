"""Modèles `discovery_scan` et `discovery_device` (miroir de 0010) : découverte LAN."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CIDR, INET, MACADDR
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz, uuid_pk

ACTIVE = ("pending", "running")


class DiscoveryScan(Base):
    __tablename__ = "discovery_scan"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','completed','failed')", name="status"),
        Index(
            "ux_discovery_scan_active",
            "site_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
        ),
        Index("ix_discovery_scan_site_created", "site_id", text("created_at DESC")),
        Index("ix_discovery_scan_cmd_id", "cmd_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("site_agent.id", ondelete="SET NULL"))
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text)
    probe_ports: Mapped[bool] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(Text)
    networks: Mapped[Any] = mapped_column(ARRAY(CIDR), server_default=text("'{}'"))
    truncated: Mapped[bool] = mapped_column(server_default=text("false"))
    capped: Mapped[bool] = mapped_column(server_default=text("false"))
    cmd_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = tstz()
    finished_at: Mapped[datetime | None] = tstz(nullable=True, now=False)


class DiscoveryDevice(Base):
    __tablename__ = "discovery_device"
    __table_args__ = (
        UniqueConstraint("scan_id", "ip", name="uq_discovery_device_scan_ip"),
        CheckConstraint(
            "char_length(vendor) <= 128 AND char_length(model) <= 128 AND char_length(name) <= 128",
            name="text",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    scan_id: Mapped[UUID] = mapped_column(ForeignKey("discovery_scan.id", ondelete="CASCADE"))
    ip: Mapped[Any] = mapped_column(INET)
    mac: Mapped[str | None] = mapped_column(MACADDR)
    vendor: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    protocols: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    suggested_drivers: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    confidence: Mapped[str] = mapped_column(Text)
