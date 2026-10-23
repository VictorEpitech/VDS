"""Migrations Alembic : branche `core` + une branche par module (`<id>/server/migrations`)."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

SERVER_DIR = Path(__file__).resolve().parents[2]


def make_config(modules_dir: Path, database_url: str | None = None) -> Config:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    locations = [SERVER_DIR / "migrations" / "versions"]
    locations += sorted(p for p in modules_dir.glob("*/server/migrations/versions") if p.is_dir())
    cfg.set_main_option("version_locations", os.pathsep.join(str(p) for p in locations))
    if database_url:
        os.environ["VDS_DATABASE_URL"] = database_url
    return cfg


def upgrade(modules_dir: Path, database_url: str | None = None) -> None:
    command.upgrade(make_config(modules_dir, database_url), "heads")


def main() -> None:
    from vds_server.settings import Settings

    settings = Settings()
    upgrade(settings.modules_dir, settings.database_url)


if __name__ == "__main__":
    main()
