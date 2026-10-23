import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from vds_sdk.bus import Event
from vds_server.config.api import ConfigDeps, build_router
from vds_server.config.schemas import StaticSchemaProvider
from vds_server.config.service import ConfigError, ConfigService
from vds_server.db.models import ConfigOverride, Organization, Screen, ScreenGroup, Site
from vds_server.main import create_app
from vds_server.settings import Settings

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "volume": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "default": 50,
            "title": "t",
            "description": "d",
            "x-vds-level": "simple",
        },
        "label": {
            "type": "string",
            "default": "x",
            "title": "t",
            "description": "d",
            "x-vds-level": "simple",
            "x-vds-scopes": ["organization", "site"],
        },
        "url": {
            "type": "string",
            "default": "http://d",
            "title": "t",
            "description": "d",
            "x-vds-level": "advanced",
            "x-vds-env": "TEST_URL",
        },
        "token": {
            "type": "string",
            "default": "",
            "title": "t",
            "description": "d",
            "x-vds-level": "advanced",
            "x-vds-env": "TEST_TOKEN",
            "x-vds-secret": True,
        },
    },
}


@dataclass
class World:
    org: Organization
    site: Site
    group: ScreenGroup
    screen: Screen
    other_screen: Screen


class FakeBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)

    def subscribe(self, name: str, handler: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
async def session(conn: AsyncConnection) -> AsyncSession:
    return AsyncSession(bind=conn, expire_on_commit=False)


@pytest.fixture
async def world(session: AsyncSession) -> World:
    org = Organization(name="Campus")
    session.add(org)
    await session.flush()
    site = Site(organization_id=org.id, name="Moulins", slug="moulins")
    session.add(site)
    await session.flush()
    group = ScreenGroup(site_id=site.id, name="Accueil")
    session.add(group)
    await session.flush()
    s1 = Screen(site_id=site.id, group_id=group.id, name="S1")
    s2 = Screen(site_id=site.id, name="S2")
    session.add_all([s1, s2])
    await session.flush()
    return World(org, site, group, s1, s2)


def svc(
    session: AsyncSession, bus: FakeBus | None = None, env: dict[str, str] | None = None
) -> ConfigService:
    return ConfigService(session, StaticSchemaProvider({"mod": SCHEMA}), bus, env or {})


async def test_four_levels_and_provenance(session: AsyncSession, world: World) -> None:
    s = svc(session)
    r = await s.resolve("screen", world.screen.id, "mod")
    assert r.values["volume"].value == 50 and r.values["volume"].origin.level == "default"
    await s.set_override("organization", world.org.id, "mod", "volume", 10)
    await s.set_override("site", world.site.id, "mod", "volume", 20)
    r = await s.resolve("screen", world.screen.id, "mod")
    v = r.values["volume"]
    assert (v.value, v.origin.level, v.origin.name) == (20, "site", "Moulins")
    await s.set_override("group", world.group.id, "mod", "volume", 30)
    v = (await s.resolve("screen", world.screen.id, "mod")).values["volume"]
    assert (v.value, v.origin.level, v.origin.name) == (30, "group", "Accueil")
    await s.set_override("screen", world.screen.id, "mod", "volume", 40)
    v = (await s.resolve("screen", world.screen.id, "mod")).values["volume"]
    assert (v.value, v.origin.level, v.inherited, v.inherited_origin.level) == (
        40,
        "screen",
        30,
        "group",
    )
    # écran sans groupe : le groupe d'un autre écran ne s'applique pas
    v = (await s.resolve("screen", world.other_screen.id, "mod")).values["volume"]
    assert (v.value, v.origin.level) == (20, "site")
    # organisation : seule sa propre valeur
    v = (await s.resolve("organization", world.org.id, "mod")).values["volume"]
    assert (v.value, v.origin.level, v.inherited, v.inherited_origin.level) == (
        10,
        "organization",
        50,
        "default",
    )


async def test_reset_to_inheritance(session: AsyncSession, world: World) -> None:
    bus = FakeBus()
    s = svc(session, bus)
    await s.set_override("site", world.site.id, "mod", "volume", 20)
    await s.set_override("screen", world.screen.id, "mod", "volume", 40)
    assert await s.delete_override("screen", world.screen.id, "mod", "volume") is True
    v = (await s.resolve("screen", world.screen.id, "mod")).values["volume"]
    assert (v.value, v.origin.level) == (20, "site")
    assert await s.delete_override("screen", world.screen.id, "mod", "volume") is False
    assert [e.name for e in bus.events] == ["config.changed"] * 3
    assert bus.events[0].payload["scope_level"] == "site"


async def test_updated_at_and_upsert(session: AsyncSession, world: World) -> None:
    s = svc(session)
    await s.set_override("site", world.site.id, "mod", "volume", 20)
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    first = row.updated_at
    await session.execute(update(ConfigOverride).values(updated_at=first.replace(year=2000)))
    await s.set_override("site", world.site.id, "mod", "volume", 21, actor_id=None)
    await session.refresh(row)
    assert row.value == 21 and row.updated_at.year > 2000
    assert len((await session.execute(select(ConfigOverride))).scalars().all()) == 1


async def test_scopes_respected(session: AsyncSession, world: World) -> None:
    s = svc(session)
    with pytest.raises(ConfigError) as e:
        await s.set_override("screen", world.screen.id, "mod", "label", "y")
    assert e.value.code == "config.scope_not_allowed"
    await s.set_override("site", world.site.id, "mod", "label", "y")
    v = (await s.resolve("screen", world.screen.id, "mod")).values["label"]
    assert v.value == "y" and v.overridable is False
    assert (await s.resolve("site", world.site.id, "mod")).values["label"].overridable is True


async def test_invalid_write_refused(session: AsyncSession, world: World) -> None:
    s = svc(session)
    for key, value, code in [
        ("volume", 101, "config.value_invalid"),
        ("volume", "x", "config.value_invalid"),
        ("nope", 1, "config.key_unknown"),
        ("url", "http://x", "config.key_from_env"),
    ]:
        with pytest.raises(ConfigError) as e:
            await s.set_override("site", world.site.id, "mod", key, value)
        assert e.value.code == code
    with pytest.raises(ConfigError) as e:
        await s.resolve("site", world.site.id, "absent")
    assert e.value.code == "config.namespace_unknown"


async def test_stored_value_became_invalid(session: AsyncSession, world: World) -> None:
    s = svc(session)
    await s.set_override("site", world.site.id, "mod", "volume", 20)
    await s.set_override("group", world.group.id, "mod", "volume", 90)
    # le schéma évolue : maximum abaissé à 50 -> 90 devient invalide, 20 reste valide
    new = {
        **SCHEMA,
        "properties": {
            **SCHEMA["properties"],
            "volume": {**SCHEMA["properties"]["volume"], "maximum": 50},
        },
    }
    s2 = ConfigService(session, StaticSchemaProvider({"mod": new}), None, {})
    r = await s2.resolve("screen", world.screen.id, "mod")
    v = r.values["volume"]
    assert (v.value, v.origin.level) == (20, "site")
    assert [(w.code, w.key, w.level) for w in r.warnings] == [
        ("config.warning.value_invalid", "volume", "group")
    ]
    # clé disparue du schéma : signalée, et la surcharge reste supprimable
    gone = {
        **SCHEMA,
        "properties": {k: p for k, p in SCHEMA["properties"].items() if k != "volume"},
    }
    s3 = ConfigService(session, StaticSchemaProvider({"mod": gone}), None, {})
    r = await s3.resolve("screen", world.screen.id, "mod")
    assert {w.code for w in r.warnings} == {"config.warning.key_unknown"}
    assert await s3.delete_override("group", world.group.id, "mod", "volume") is True


async def test_env_and_secret(session: AsyncSession, world: World) -> None:
    s = svc(session, env={"TEST_URL": "http://env", "TEST_TOKEN": "s3cret"})
    r = await s.resolve("screen", world.screen.id, "mod")
    assert (r.values["url"].value, r.values["url"].origin.level, r.values["url"].overridable) == (
        "http://env",
        "env",
        False,
    )
    t = r.values["token"]
    assert t.value is None and t.inherited is None and t.secret is True


async def test_core_namespace_defaults(session: AsyncSession, world: World) -> None:
    r = await svc(session).resolve("site", world.site.id, "core")
    assert r.values["timezone"].value == "Europe/Paris"


@pytest.mark.parametrize("base", ["/", "/espace/affichage"])
async def test_api_roundtrip(session: AsyncSession, world: World, base: str) -> None:
    async def get_session() -> AsyncIterator[AsyncSession]:
        yield session

    router = build_router(ConfigDeps(get_session, StaticSchemaProvider({"mod": SCHEMA})))
    app = create_app(Settings(base_path=base), extra_routers=[router])
    p = "" if base == "/" else base
    url = f"{p}/api/v1/config/site/{world.site.id}/mod"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://t",
        headers={"Sec-Fetch-Site": "same-origin"},
    ) as c:
        assert (await c.put(f"{url}/volume", json={"value": 33})).status_code == 204
        body = (await c.get(url)).json()
        assert body["values"]["volume"]["value"] == 33
        assert body["values"]["volume"]["origin"]["level"] == "site"
        assert body["values"]["volume"]["origin"]["name"] == "Moulins"
        assert body["values"]["volume"]["inherited"] == 50
        assert body["warnings"] == []
        bad = await c.put(f"{url}/volume", json={"value": 500})
        assert bad.status_code == 422 and bad.json()["code"] == "config.value_invalid"
        scope = await c.put(
            f"{p}/api/v1/config/screen/{world.screen.id}/mod/label", json={"value": "z"}
        )
        assert scope.status_code == 422 and scope.json()["code"] == "config.scope_not_allowed"
        assert (await c.delete(f"{url}/volume")).status_code == 204
        assert (await c.get(url)).json()["values"]["volume"]["origin"]["level"] == "default"
        missing = await c.get(f"{p}/api/v1/config/site/{UUID(int=1)}/mod")
        assert missing.status_code == 404 and missing.json()["code"] == "config.target_unknown"


async def test_secret_never_leaks_in_api_nor_repr(
    session: AsyncSession, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    from vds_server.config.api import _out

    with caplog.at_level("DEBUG"):
        r = await svc(session, env={"TEST_TOKEN": "s3cret-value"}).resolve(
            "screen", world.screen.id, "mod"
        )
        out = _out(r)
    assert "s3cret-value" not in out.model_dump_json()
    assert "s3cret-value" not in repr(r) and "s3cret-value" not in caplog.text
    tok = out.values["token"]
    assert tok.value is None and tok.is_set is True and tok.origin.level == "env"
    assert out.values["url"].is_set is None
    unset = _out(await svc(session, env={}).resolve("screen", world.screen.id, "mod"))
    assert unset.values["token"].is_set is False


async def test_owner_reader_gets_secretstr(session: AsyncSession, world: World) -> None:
    from pydantic import SecretStr
    from vds_server.config.reader import ModuleConfigReader

    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    reader = ModuleConfigReader(maker, StaticSchemaProvider({"mod": SCHEMA}), "mod")
    os.environ["TEST_TOKEN"] = "s3cret-value"  # noqa: S105
    try:
        cfg = await reader.resolve()
    finally:
        del os.environ["TEST_TOKEN"]
    tok = cfg["token"]
    assert isinstance(tok, SecretStr) and tok.get_secret_value() == "s3cret-value"
    assert "s3cret-value" not in repr(cfg) and "s3cret-value" not in str(tok)
