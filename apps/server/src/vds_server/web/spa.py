"""config.js d'exécution, page d'accueil admin (<base href>) et repli SPA."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

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


PLAYER_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
    "connect-src 'self'; frame-src https:; worker-src 'self'; object-src 'none'; "
    "base-uri 'self'"
)


# CSP de l'admin (backlog sécurité n°10) : aucun script ni style en ligne (config.js, JS et CSS sont
# des fichiers `self`), aucune origine tierce, jamais encadrée. Identique aux deux VDS_BASE_PATH.
ADMIN_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; media-src 'self'; font-src 'self'; "
    "connect-src 'self'; frame-src 'none'; worker-src 'none'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
ADMIN_HEADERS = {"Content-Security-Policy": ADMIN_CSP, "X-Content-Type-Options": "nosniff"}


def inject_base_href(html: str, settings: Settings, sub: str = "") -> str:
    tag = f'<base href="{settings.prefix}/{sub}">'
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

    player_dir = settings.player_dir
    player_html: str | None = None
    if player_dir is not None and (player_dir / "index.html").is_file():
        player_html = inject_base_href(
            (player_dir / "index.html").read_text("utf-8"), settings, "player/"
        )

    def _player_file(rel: str) -> Path | None:
        if player_dir is None or not rel:
            return None
        root = player_dir.resolve()
        candidate = (root / rel).resolve()
        if candidate.is_file() and candidate.is_relative_to(root) and rel != "index.html":
            return candidate
        return None

    @router.get("/player", include_in_schema=False)
    async def player_redirect() -> Response:
        return RedirectResponse(f"{settings.prefix}/player/", status_code=307)

    @router.get("/player/{path:path}", include_in_schema=False)
    async def player(path: str) -> Response:
        # Ressources de l'application (JS, CSS, sw.js) ; `<base>/player/` seul = page du player.
        f = _player_file(path)
        if f is not None:
            return FileResponse(f, headers={"Content-Security-Policy": PLAYER_CSP})
        if player_html is None or path:
            raise HTTPException(status_code=404, detail="errors.not_found")
        return HTMLResponse(player_html, headers={"Content-Security-Policy": PLAYER_CSP})

    modules_root = settings.modules_dir.resolve()

    @router.get("/modules/{module_id}/assets/{rel:path}", include_in_schema=False)
    async def module_asset(module_id: str, rel: str) -> Response:
        # Ressources publiques : thèmes (tokens, polices, icônes, logo) et code `player.js` des
        # content-types (aucun secret ; le player les met en cache pour le hors ligne).
        if not module_id.startswith(("theme.", "content.")) or not _MODULE_ID.fullmatch(module_id):
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
            return FileResponse(f, headers=ADMIN_HEADERS)
        return HTMLResponse(index_html, headers=ADMIN_HEADERS)

    return router
