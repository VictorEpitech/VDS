"""config.js d'exécution, page d'accueil admin (<base href>) et repli SPA."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from vds_server.settings import Settings

_MODULE_ID = re.compile(r"[a-z0-9][a-z0-9.-]*")
RESERVED = {"api", "ws", "media", "modules", "player", "health"}


AuthInfo = Callable[[], dict[str, Any]]


def no_auth() -> dict[str, Any]:
    return {"loginUrl": None, "logoutUrl": None, "links": []}


def build_config_js(settings: Settings, auth_info: AuthInfo = no_auth) -> str:
    p = settings.prefix
    ws_origin = settings.public_origin.replace("http", "ws", 1) if settings.public_origin else ""
    cfg = {
        "basePath": settings.base_path,
        "apiBase": f"{p}/api/v1",
        "wsBase": f"{ws_origin}{p}/ws",
        "defaultLocale": settings.default_locale,
        "version": settings.version,
        "theme": {
            "id": settings.theme,
            "cssUrl": f"{p}/modules/{settings.theme}/assets/theme.css",
            "icons": None,
            "logo": None,
        },
        "auth": {
            "provider": settings.auth_provider,
            **auth_info(),
        },
    }
    return "window.__VDS__ = " + json.dumps(cfg).replace("</", "<\\/") + ";\n"


def inject_base_href(html: str, settings: Settings) -> str:
    tag = f'<base href="{settings.prefix}/">'
    lower = html.lower()
    i = lower.find("<head")
    if i == -1:
        return tag + html
    j = html.find(">", i) + 1
    return html[:j] + tag + html[j:]


def build_router(settings: Settings, auth_info: AuthInfo = no_auth) -> APIRouter:
    router = APIRouter()

    @router.get("/config.js", include_in_schema=False)
    async def config() -> Response:
        # Évalué à chaque requête : le provider n'est actif qu'après le démarrage.
        return Response(build_config_js(settings, auth_info), media_type="text/javascript")

    admin_dir = settings.admin_dir
    index_html: str | None = None
    if admin_dir is not None and (admin_dir / "index.html").is_file():
        index_html = inject_base_href((admin_dir / "index.html").read_text("utf-8"), settings)

    def _static(rel: str) -> Path | None:
        if admin_dir is None or not rel:
            return None
        root = admin_dir.resolve()
        candidate = (root / rel).resolve()
        if (
            candidate.is_file()
            and candidate.is_relative_to(root)
            and candidate.name != "index.html"
        ):
            return candidate
        return None

    modules_root = settings.modules_dir.resolve()

    @router.get("/modules/{module_id}/assets/{rel:path}", include_in_schema=False)
    async def module_asset(module_id: str, rel: str) -> Response:
        # Seuls les thèmes exposent des ressources publiques (tokens, polices, icônes, logo).
        if not module_id.startswith("theme.") or not _MODULE_ID.fullmatch(module_id):
            raise HTTPException(status_code=404, detail="errors.not_found")
        root = (modules_root / module_id / "assets").resolve()
        candidate = (root / rel).resolve()
        if not (candidate.is_file() and candidate.is_relative_to(root)):
            raise HTTPException(status_code=404, detail="errors.not_found")
        return FileResponse(candidate)

    @router.get("/{path:path}", include_in_schema=False)
    async def admin(path: str) -> Response:
        if path.split("/", 1)[0] in RESERVED or index_html is None:
            raise HTTPException(status_code=404, detail="errors.not_found")
        f = _static(path)
        if f is not None:
            return FileResponse(f)
        return HTMLResponse(index_html)

    return router
