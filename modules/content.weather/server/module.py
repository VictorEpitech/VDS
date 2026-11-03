"""content.weather : météo d'un lieu via MET Norway Locationforecast 2.0 (sans clé, CC BY 4.0).

Le serveur récupère et normalise ; le player ne contacte jamais MET. Ne dépend que de `vds_sdk`.
"""

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID

from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, PlayerItemSpec
from vds_sdk.server.feeds import FeedRequest
from vds_sdk.server.http import SafeHttp
from vds_sdk.server.media import MediaInfo

MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat:.4f}&lon={lon:.4f}"
MAX_BYTES = 512 * 1024
DEFAULT_REFRESH_MIN = 60
MIN_REFRESH_MIN = 30
MAX_DAYS = 3
ATTRIBUTION = "MET Norway"
_SUFFIXES = ("_day", "_night", "_polartwilight")


def _number(value: object, lo: float, hi: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not lo <= value <= hi:
        raise ContractError(code)
    return float(value)


def _base_symbol(code: str) -> str:
    for suffix in _SUFFIXES:
        if code.endswith(suffix):
            return code[: -len(suffix)]
    return code


def _symbol(data: Mapping[str, Any]) -> str | None:
    for key in ("next_1_hours", "next_6_hours", "next_12_hours"):
        code = ((data.get(key) or {}).get("summary") or {}).get("symbol_code")
        if isinstance(code, str) and code:
            return code
    return None


def _temp(data: Mapping[str, Any]) -> float | None:
    value = ((data.get("instant") or {}).get("details") or {}).get("air_temperature")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def normalize(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Réponse MET compacte -> `{now, days, attribution}` (dates en UTC, températures en °C)."""
    series = ((payload.get("properties") or {}).get("timeseries")) or []
    entries: list[tuple[datetime, Mapping[str, Any]]] = []
    for item in series:
        try:
            when = datetime.fromisoformat(str(item["time"]).replace("Z", "+00:00"))
            entries.append((when.astimezone(UTC), item["data"]))
        except (KeyError, ValueError, TypeError):
            continue
    if not entries:
        raise ContractError("content.weather.invalid_response")
    entries.sort(key=lambda e: e[0])
    first_time, first = entries[0]
    now_temp = _temp(first)
    now_symbol = _symbol(first)
    if now_temp is None or now_symbol is None:
        raise ContractError("content.weather.invalid_response")
    by_day: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = defaultdict(list)
    for when, data in entries:
        by_day[when.date().isoformat()].append((when, data))
    days: list[dict[str, Any]] = []
    for date in sorted(by_day)[:MAX_DAYS]:
        rows = by_day[date]
        temps = [t for _, d in rows if (t := _temp(d)) is not None]
        if not temps:
            continue
        noon = min(
            (r for r in rows if _symbol(r[1]) is not None),
            key=lambda r: abs(r[0].hour - 12),
            default=None,
        )
        symbol = _base_symbol(_symbol(noon[1]) or "") if noon else ""
        days.append(
            {
                "date": date,
                "min_c": round(min(temps), 1),
                "max_c": round(max(temps), 1),
                "symbol": symbol or _base_symbol(now_symbol),
            }
        )
    return {
        "now": {"temp_c": round(now_temp, 1), "symbol": now_symbol},
        "days": days,
        "updated": first_time.isoformat().replace("+00:00", "Z"),
        "attribution": ATTRIBUTION,
    }


class ContentWeather(ContentType):
    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._refresh_s = DEFAULT_REFRESH_MIN * 60

    async def setup(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        await self._read_config()

    async def _read_config(self) -> dict[str, Any]:
        if self._ctx is None:
            raise ContractError("content.weather.not_ready")
        cfg = await self._ctx.config.resolve()
        minutes = cfg.get("refresh_min")
        minutes = minutes if isinstance(minutes, int) else DEFAULT_REFRESH_MIN
        self._refresh_s = max(minutes, MIN_REFRESH_MIN) * 60
        return cfg

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        if self._ctx is None:
            raise ContractError("content.weather.not_ready")
        lat = round(_number(props.get("latitude"), -90, 90, "content.weather.invalid_location"), 4)
        lon = round(
            _number(props.get("longitude"), -180, 180, "content.weather.invalid_location"), 4
        )
        label = str(props.get("label") or "").strip()
        if not label:
            raise ContractError("content.weather.invalid_label")
        days = props.get("days", 1)
        units = props.get("units", "metric")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
            raise ContractError("content.weather.invalid_days")
        if units not in ("metric", "imperial"):
            raise ContractError("content.weather.invalid_units")
        max_age = props.get("max_age_s", 21600)
        if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 600:
            raise ContractError("content.weather.invalid_max_age")
        feed_id = await self._ctx.core.feeds.ensure(
            FeedRequest(params={"lat": lat, "lon": lon}, refresh_s=self._refresh_s)
        )
        return PlayerItemSpec(
            props={
                "label": label,
                "days": days,
                "units": units,
                "max_age_s": max_age,
                "feed_id": feed_id,
            },
            assets=[],
            feeds=[feed_id],
        )

    async def fetch_feed(self, req: FeedRequest, http: SafeHttp) -> dict[str, Any]:
        cfg = await self._read_config()
        lat = _number(req.params.get("lat"), -90, 90, "content.weather.invalid_location")
        lon = _number(req.params.get("lon"), -180, 180, "content.weather.invalid_location")
        contact = str(cfg.get("contact") or "").strip()
        resp = await http.get(
            MET_URL.format(lat=lat, lon=lon),
            max_bytes=MAX_BYTES,
            headers={"User-Agent": contact} if contact else None,
            accept=("application/json",),
        )
        try:
            payload = json.loads(resp.body)
        except ValueError as exc:
            raise ContractError("content.weather.invalid_response") from exc
        if not isinstance(payload, dict):
            raise ContractError("content.weather.invalid_response")
        data = normalize(payload)
        expires = resp.headers.get("expires")
        if expires:  # MET : ne pas redemander avant `Expires` (le noyau prend le maximum)
            try:
                delta = (parsedate_to_datetime(expires) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):
                delta = 0
            if delta > 0:
                data["_next_after_s"] = int(delta)
        return data
