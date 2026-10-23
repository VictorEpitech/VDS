"""Cookies : toujours Path=<base>/ (ou /)."""

from typing import Literal

from fastapi import Response

from vds_server.settings import Settings


def cookie_path(settings: Settings) -> str:
    return f"{settings.prefix}/"


def set_cookie(
    response: Response,
    settings: Settings,
    key: str,
    value: str,
    *,
    max_age: int | None = None,
    samesite: Literal["lax", "strict"] = "lax",
) -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        path=cookie_path(settings),
        httponly=True,
        secure=True,
        samesite=samesite,
    )


def delete_cookie(response: Response, settings: Settings, key: str) -> None:
    response.delete_cookie(
        key, path=cookie_path(settings), httponly=True, secure=True, samesite="lax"
    )
