from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from vds_server.db.models import Organization, Site


@pytest.fixture
async def session(conn: AsyncConnection) -> AsyncSession:
    return AsyncSession(bind=conn, expire_on_commit=False)


async def _refused(session: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(text(sql), params)


async def test_power_constraints(session: AsyncSession) -> None:
    org = Organization(name="Campus")
    session.add(org)
    await session.flush()
    site = Site(organization_id=org.id, name="A", slug="a")
    session.add(site)
    await session.flush()
    tz = await session.execute(text("SELECT timezone FROM site WHERE id=:i"), {"i": site.id})
    assert tz.scalar() == "Europe/Paris"

    ins = "INSERT INTO site_agent (site_id, name, token_hash, token_prefix) VALUES (:s,'a',:h,'p')"
    await session.execute(text(ins), {"s": site.id, "h": "h1"})
    await _refused(session, ins, {"s": site.id, "h": "h2"})  # un seul agent actif par site
    await session.execute(text("UPDATE site_agent SET revoked_at=now()"))
    await session.execute(text(ins), {"s": site.id, "h": "h3"})

    inc = (
        "INSERT INTO incident (kind, severity, subject_type, subject_id, opened_at) "
        "VALUES ('k', :sev, 'screen', 'x', now())"
    )
    await _refused(session, inc, {"sev": "bad"})
    await session.execute(text(inc), {"sev": "info"})
    await _refused(session, inc, {"sev": "info"})  # incident ouvert unique

    await session.execute(text("INSERT INTO power_plan (name) VALUES ('p')"))
    rule = (
        "INSERT INTO power_plan_rule (plan_id, weekdays, start_minute, end_minute) "
        "SELECT id, 1, :a, :b FROM power_plan"
    )
    await session.execute(text(rule), {"a": 480, "b": 1080})
    await _refused(session, rule, {"a": 600, "b": 600})
