"""État d'appareil exposé avec chaque écran (présence calculée, aucun secret)."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.db.models import Screen, ScreenDevice
from vds_server.inventory import schemas as sc
from vds_server.player import kiosk
from vds_server.player.schemas import KioskReport
from vds_server.player.service import HEARTBEAT_INTERVAL_S

ONLINE_WINDOW = timedelta(seconds=HEARTBEAT_INTERVAL_S * 3)


async def latest_devices(session: AsyncSession, screen_ids: Iterable[UUID]) -> dict[UUID, Any]:
    """Appareil actif de chaque écran, à défaut le dernier révoqué."""
    ids = list(screen_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(ScreenDevice)
            .where(ScreenDevice.screen_id.in_(ids))
            .ext(distinct_on(ScreenDevice.screen_id))
            .order_by(
                ScreenDevice.screen_id,
                ScreenDevice.revoked_at.is_(None).desc(),
                ScreenDevice.paired_at.desc(),
            )
        )
    ).scalars()
    return {r.screen_id: r for r in rows}


def presence(dev: ScreenDevice, now: datetime) -> sc.Presence:
    if dev.revoked_at is not None:
        return "revoked"
    if dev.last_seen_at is None:
        return "unseen"
    return "online" if now - dev.last_seen_at <= ONLINE_WINDOW else "offline"


def device_state(dev: ScreenDevice | None, now: datetime | None = None) -> sc.DeviceStateOut | None:
    if dev is None:
        return None
    hb = dev.last_heartbeat if isinstance(dev.last_heartbeat, dict) else None
    heartbeat = None
    if hb is not None:
        raw_cache = hb.get("cache")
        missing = raw_cache.get("missing") if isinstance(raw_cache, dict) else None
        rev, up = hb.get("revision"), hb.get("uptime_s")
        heartbeat = sc.DeviceHeartbeatOut(
            revision=rev if isinstance(rev, str) else None,
            stale=hb.get("stale") is True,
            uptime_s=up if isinstance(up, int) and not isinstance(up, bool) else None,
            missing_assets=len(missing) if isinstance(missing, list) else 0,
        )
    return sc.DeviceStateOut(
        id=dev.id,
        presence=presence(dev, now or datetime.now(UTC)),
        platform=dev.platform,
        model=dev.model,
        app_version=dev.app_version,
        paired_at=dev.paired_at,
        last_seen_at=dev.last_seen_at,
        revoked_at=dev.revoked_at,
        heartbeat=heartbeat,
    )


def kiosk_state(
    request: kiosk.KioskRequest, dev: ScreenDevice | None, now: datetime
) -> sc.KioskOut:
    doc = kiosk.served(request, now)
    out = sc.KioskOut(
        requested=doc["mode"], until=request.until if doc["mode"] == "maintenance" else None
    )
    hb = dev.last_heartbeat if dev is not None and dev.revoked_at is None else None
    raw = hb.get("kiosk") if isinstance(hb, dict) else None
    if isinstance(raw, dict):
        try:
            report = KioskReport.model_validate(raw)
        except ValueError:
            return out
        out.applied, out.enforcement = report.mode, report.enforcement
    return out


async def screens_out(session: AsyncSession, screens: Iterable[Screen]) -> list[sc.ScreenOut]:
    rows = list(screens)
    devices = await latest_devices(session, (r.id for r in rows))
    requests = await kiosk.load_requests(session, rows)
    now = datetime.now(UTC)
    out = []
    for r in rows:
        item = sc.ScreenOut.model_validate(r)
        dev = devices.get(r.id)
        item.device = device_state(dev, now)
        item.kiosk = kiosk_state(requests[r.id], dev, now)
        out.append(item)
    return out
