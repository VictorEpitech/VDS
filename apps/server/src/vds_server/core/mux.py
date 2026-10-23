"""Montage à chaud des routes de modules sous `<base>/api/v1/m/<id>/`."""

from fastapi import APIRouter, FastAPI
from starlette.routing import Mount, Router

from vds_server.core.errors import install_error_handlers


class ModuleMux:
    def __init__(self) -> None:
        self.router = Router()

    def add(self, module_id: str, routes: APIRouter) -> None:
        self.remove(module_id)
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        install_error_handlers(app)
        app.include_router(routes)
        self.router.routes.append(Mount(f"/{module_id}", app=app, name=module_id))

    def remove(self, module_id: str) -> None:
        self.router.routes[:] = [
            r for r in self.router.routes if getattr(r, "name", None) != module_id
        ]
