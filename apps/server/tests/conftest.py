import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from vds_server.migrate import make_config

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_MODULES = SERVER_DIR.parents[1] / "modules"


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    return make_config(REPO_MODULES)


@pytest.fixture(scope="session")
def migrated_db_url(alembic_cfg: Config) -> str:
    """Base PostgreSQL 17 vide + migration complète. URL (et mot de passe) via l'environnement."""
    url = os.environ.get("VDS_TEST_DATABASE_URL")
    if not url:
        pytest.fail("VDS_TEST_DATABASE_URL non défini (voir `make test-db`)")

    async def reset() -> None:
        engine = create_async_engine(url, poolclass=pool.NullPool)
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    os.environ["VDS_DATABASE_URL"] = url
    command.upgrade(alembic_cfg, "heads")
    return url


@pytest.fixture
async def conn(migrated_db_url: str) -> AsyncIterator[AsyncConnection]:
    """Connexion dans une transaction annulée en fin de test."""
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        trans = await connection.begin()
        yield connection
        await trans.rollback()
    await engine.dispose()
