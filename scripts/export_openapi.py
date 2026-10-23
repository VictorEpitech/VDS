"""Exporte l'OpenAPI du serveur (sans DB : le moteur SQLAlchemy est paresseux).

Usage : uv run python scripts/export_openapi.py <sortie.json>
Les routes de module (/api/v1/m/<id>/) sont montées dynamiquement et absentes du schéma.
"""

import json
import os
import sys
from pathlib import Path

os.environ["VDS_BASE_PATH"] = "/"
os.environ.setdefault(
    "VDS_DATABASE_URL", "postgresql+asyncpg://openapi:openapi@127.0.0.1:1/openapi"
)

from fastapi import FastAPI  # noqa: E402
from starlette.routing import Mount  # noqa: E402
from vds_server.core.runtime import Runtime  # noqa: E402
from vds_server.main import create_app  # noqa: E402
from vds_server.settings import Settings  # noqa: E402


def main(out: Path) -> None:
    settings = Settings()
    root = create_app(settings, runtime=Runtime(settings))
    inner = next(r.app for r in root.routes if isinstance(r, Mount) and isinstance(r.app, FastAPI))
    out.write_text(json.dumps(inner.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
