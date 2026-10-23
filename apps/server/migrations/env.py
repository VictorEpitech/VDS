"""Environnement Alembic (async). URL : VDS_DATABASE_URL, sinon alembic -x url=..."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from vds_server.db import models  # noqa: F401
from vds_server.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get(
        "VDS_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("VDS_DATABASE_URL non défini")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(_run_async())
