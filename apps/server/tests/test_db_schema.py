import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from vds_server.db.base import Base
from vds_server.db.models import ConfigOverride, Organization, Screen, ScreenGroup, Site


async def _seed(s: AsyncSession) -> tuple[Organization, Site, Site]:
    org = Organization(name="Campus")
    s.add(org)
    await s.flush()
    a = Site(organization_id=org.id, name="A", slug="a")
    b = Site(organization_id=org.id, name="B", slug="b")
    s.add_all([a, b])
    await s.flush()
    return org, a, b


@pytest.fixture
async def session(conn: AsyncConnection) -> AsyncSession:
    return AsyncSession(bind=conn, expire_on_commit=False)


async def test_second_organization_refused(session: AsyncSession) -> None:
    session.add(Organization(name="Un"))
    await session.flush()
    session.add(Organization(name="Deux"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_group_of_other_site_refused(session: AsyncSession) -> None:
    _, a, b = await _seed(session)
    group = ScreenGroup(site_id=a.id, name="Hall")
    session.add(group)
    await session.flush()
    session.add(Screen(site_id=a.id, group_id=group.id, name="ok"))
    await session.flush()
    session.add(Screen(site_id=b.id, group_id=group.id, name="ko"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_screen_without_group_allowed(session: AsyncSession) -> None:
    _, a, _ = await _seed(session)
    session.add(Screen(site_id=a.id, name="seul"))
    await session.flush()


async def test_override_levels_and_uniqueness(session: AsyncSession) -> None:
    org, a, _ = await _seed(session)
    group = ScreenGroup(site_id=a.id, name="Hall")
    session.add(group)
    await session.flush()
    screen = Screen(site_id=a.id, group_id=group.id, name="e1")
    session.add(screen)
    await session.flush()
    rows = [
        ConfigOverride(organization_id=org.id, namespace="core", key="timezone", value="UTC"),
        ConfigOverride(site_id=a.id, namespace="core", key="timezone", value="Europe/Paris"),
        ConfigOverride(group_id=group.id, namespace="core", key="timezone", value="x"),
        ConfigOverride(screen_id=screen.id, namespace="core", key="timezone", value="y"),
    ]
    session.add_all(rows)
    await session.flush()
    for row in rows:
        await session.refresh(row)
    assert [r.scope_level for r in rows] == [0, 1, 2, 3]
    assert [r.scope_id for r in rows] == [org.id, a.id, group.id, screen.id]

    session.add(ConfigOverride(site_id=a.id, namespace="core", key="timezone", value="dup"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_override_multi_level_row_refused(session: AsyncSession) -> None:
    org, a, _ = await _seed(session)
    session.add(
        ConfigOverride(organization_id=org.id, site_id=a.id, namespace="core", key="k", value=1)
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_override_no_level_refused(session: AsyncSession) -> None:
    await _seed(session)
    session.add(ConfigOverride(namespace="core", key="k", value=1))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_user_unique_per_provider_external_id(conn: AsyncConnection) -> None:
    ins = text(
        "INSERT INTO user_account (auth_provider, external_id, display_name, role) "
        "VALUES (:p, :e, 'n', 'admin')"
    )
    await conn.execute(ins, {"p": "auth.local", "e": "1"})
    await conn.execute(ins, {"p": "auth.studio-allia", "e": "1"})
    with pytest.raises(IntegrityError):
        await conn.execute(ins, {"p": "auth.local", "e": "1"})


async def test_models_match_migration(conn: AsyncConnection) -> None:
    rows = await conn.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    )
    assert set(Base.metadata.tables) <= {r[0] for r in rows}


def test_downgrade_then_upgrade(migrated_db_url: str, alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "heads")
