"""Plan d'alimentation résolu (J + J+1) : récupération HTTP (ETag), cache, créneau courant."""

from datetime import datetime
from typing import Any, Literal

import httpx


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def current_slot(
    slots: list[dict[str, Any]], now: datetime
) -> tuple[str, Literal["on", "off"]] | None:
    """Créneau contenant `now` : (clé d'application, état). Chevauchement : `off` l'emporte."""
    hit: list[tuple[str, Literal["on", "off"]]] = []
    for s in slots:
        if parse_dt(s["from"]) <= now < parse_dt(s["to"]) and s["state"] in ("on", "off"):
            hit.append((f"{s['from']}|{s['state']}", s["state"]))
    if not hit:
        return None
    return next((h for h in hit if h[1] == "off"), hit[0])


async def fetch_plan(
    client: httpx.AsyncClient, base: str, token: str, etag: str | None
) -> tuple[dict[str, Any], str | None] | None:
    """None si 304 ; sinon (plan, etag). Lève httpx.HTTPError."""
    headers = {"Authorization": f"Bearer {token}"}
    if etag:
        headers["If-None-Match"] = etag
    r = await client.get(f"{base}/api/v1/agent/plan", headers=headers)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    return r.json(), r.headers.get("ETag")
