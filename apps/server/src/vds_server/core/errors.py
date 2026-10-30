"""Erreurs d'API : codes stables (l'admin traduit), jamais de texte destiné à l'utilisateur."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: str, status: int, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


async def _handle(_: Request, exc: Exception) -> JSONResponse:
    err = exc if isinstance(exc, ApiError) else ApiError("errors.internal", 500)
    headers = {}
    retry = err.params.get("retry_after_s")
    if err.status == 429 and isinstance(retry, int):
        headers["Retry-After"] = str(retry)
    return JSONResponse(
        status_code=err.status, content={"code": err.code, "params": err.params}, headers=headers
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle)
