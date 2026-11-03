"""Modules content.weather et content.rss (serveur) : manifeste, schémas, i18n, normalisation."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from vds_sdk import ContractError, ModuleContext
from vds_sdk.manifest import load_manifest
from vds_sdk.server.content import ContentType
from vds_sdk.server.feeds import FeedRequest, compute_feed_id
from vds_sdk.server.http import SafeHttp, SafeHttpError, SafeResponse
from vds_server.registry.runtime import load_server_module

MODULES = Path(__file__).resolve().parents[3] / "modules"
FIX = Path(__file__).parent / "fixtures"
IDS = ["content.weather", "content.rss"]


def _load(mid: str) -> ContentType:
    manifest = load_manifest(MODULES / mid / "module.json")
    module = load_server_module(MODULES / mid, manifest)
    assert isinstance(module, ContentType)
    return module


class _Feeds:
    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.requests: list[FeedRequest] = []

    async def ensure(self, req: FeedRequest) -> str:
        self.requests.append(req)
        return compute_feed_id(self.module_id, req.canonical())


class _Core:
    def __init__(self, module_id: str) -> None:
        self.feeds = _Feeds(module_id)


class _Config:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def resolve(self, scope: object = None) -> dict[str, Any]:
        return self.values


class _Ctx:
    def __init__(self, mid: str, values: dict[str, Any]) -> None:
        self.config = _Config(values)
        self.core = _Core(mid)


class FakeHttp:
    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.body, self.headers = body, headers or {}
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_s: float = 10.0,
        headers: Mapping[str, str] | None = None,
        accept: tuple[str, ...] = (),
    ) -> SafeResponse:
        self.calls.append({"url": url, "max_bytes": max_bytes, "headers": headers, "a": accept})
        return SafeResponse(status=200, headers=self.headers, body=self.body, final_url=url)


async def ready(mid: str, values: dict[str, Any] | None = None) -> tuple[ContentType, _Ctx]:
    module = _load(mid)
    ctx = _Ctx(mid, values or {})
    await module.setup(cast(ModuleContext, ctx))
    return module, ctx


def http(fake: FakeHttp) -> SafeHttp:
    return cast(SafeHttp, fake)


# --- manifeste, schémas, i18n ---------------------------------------------------


@pytest.mark.parametrize("mid", IDS)
def test_manifest_and_schemas(mid: str) -> None:
    manifest = load_manifest(MODULES / mid / "module.json")
    assert manifest.kind == "content-type" and manifest.core == ">=1.12 <2.0"
    assert set(manifest.permissions) == {"net.outbound", "content.feeds"}
    assert manifest.content is not None
    assert manifest.content.player.element == "vds-" + mid.replace(".", "-")
    assert manifest.content.requires_network is False
    for name in ("item.schema.json", "config.schema.json"):
        Draft202012Validator.check_schema(json.loads((MODULES / mid / name).read_text()))


def _flat(obj: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


@pytest.mark.parametrize("mid", IDS)
def test_i18n_covers_schema_keys_and_error_codes(mid: str) -> None:
    fr = _flat(json.loads((MODULES / mid / "i18n" / "fr.json").read_text()))
    en = _flat(json.loads((MODULES / mid / "i18n" / "en.json").read_text()))
    assert fr.keys() == en.keys() and all(fr.values()) and all(en.values())
    for name in ("config.schema.json", "item.schema.json"):
        for prop in json.loads((MODULES / mid / name).read_text())["properties"].values():
            assert prop["title"] in fr and prop["description"] in fr
    import re

    source = (MODULES / mid / "server" / "module.py").read_text()
    prefix = mid + "."
    codes = set(re.findall(r'ContractError\("(' + re.escape(prefix) + r'[a-z_]+)"', source))
    assert codes
    for code in codes:
        key = "errors." + mid.replace(".", "_") + "." + code.removeprefix(prefix)
        assert key in fr, key


# --- content.weather ------------------------------------------------------------

ITEM = {"label": "Paris", "latitude": 48.85661, "longitude": 2.35222}


async def test_weather_to_player_ensures_shared_feed() -> None:
    module, ctx = await ready("content.weather", {"refresh_min": 90})
    a = await module.to_player({**ITEM, "days": 2, "units": "imperial"}, {})
    b = await module.to_player({**ITEM, "label": "Autre"}, {})
    assert a.feeds == b.feeds and len(a.feeds) == 1  # même lieu, même flux
    assert a.props["feed_id"] == a.feeds[0] and a.props["units"] == "imperial"
    req = ctx.core.feeds.requests[0]
    assert req.params == {"lat": 48.8566, "lon": 2.3522} and req.refresh_s == 5400


async def test_weather_refresh_never_below_30_min() -> None:
    module, ctx = await ready("content.weather", {"refresh_min": 1})
    await module.to_player(ITEM, {})
    assert ctx.core.feeds.requests[0].refresh_s == 1800


@pytest.mark.parametrize(
    "over",
    [
        {"latitude": 91}, {"longitude": -181}, {"latitude": "48"}, {"latitude": True},
        {"label": "  "}, {"days": 4}, {"days": 0}, {"units": "kelvin"}, {"max_age_s": 5},
    ],
)  # fmt: skip
async def test_weather_rejects_invalid_item(over: dict[str, Any]) -> None:
    module, ctx = await ready("content.weather")
    with pytest.raises(ContractError):
        await module.to_player({**ITEM, **over}, {})
    assert not ctx.core.feeds.requests


async def test_weather_fetch_normalizes_recorded_response() -> None:
    module, _ = await ready("content.weather", {"contact": "ops@example.org"})
    fake = FakeHttp((FIX / "met_locationforecast.json").read_bytes())
    data = await module.fetch_feed(
        FeedRequest(params={"lat": 48.8566, "lon": 2.3522}, refresh_s=3600), http(fake)
    )
    call = fake.calls[0]
    assert call["url"] == (
        "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=48.8566&lon=2.3522"
    )
    assert call["max_bytes"] == 512 * 1024 and call["headers"] == {"User-Agent": "ops@example.org"}
    assert data["attribution"] == "MET Norway"
    assert isinstance(data["now"]["temp_c"], float)
    assert data["now"]["symbol"] == "partlycloudy_day"  # symbole brut (variante jour/nuit)
    assert [d["date"] for d in data["days"]] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    for day in data["days"]:
        assert day["min_c"] <= day["max_c"] and not day["symbol"].endswith(("_day", "_night"))
    assert len(json.dumps(data)) < 64 * 1024  # tient dans le plafond du noyau


async def test_weather_expires_header_lengthens_refresh() -> None:
    module, _ = await ready("content.weather")
    body = (FIX / "met_locationforecast.json").read_bytes()
    req = FeedRequest(params={"lat": 1.0, "lon": 2.0}, refresh_s=1800)
    far = FakeHttp(body, {"expires": "Fri, 01 Jan 2100 00:00:00 GMT"})
    assert (await module.fetch_feed(req, http(far)))["_next_after_s"] > 0
    past = FakeHttp(body, {"expires": "Mon, 01 Jan 2001 00:00:00 GMT"})
    assert "_next_after_s" not in await module.fetch_feed(req, http(past))
    assert "_next_after_s" not in await module.fetch_feed(req, http(FakeHttp(body)))


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"properties":{"timeseries":[]}}'])
async def test_weather_invalid_response(body: bytes) -> None:
    module, _ = await ready("content.weather")
    req = FeedRequest(params={"lat": 1.0, "lon": 2.0}, refresh_s=1800)
    with pytest.raises(ContractError) as exc:
        await module.fetch_feed(req, http(FakeHttp(body)))
    assert exc.value.code == "content.weather.invalid_response"


async def test_weather_http_error_propagates() -> None:
    module, _ = await ready("content.weather")

    class Boom:
        async def get(self, url: str, **kw: Any) -> SafeResponse:
            raise SafeHttpError("net.http_status", "503")

    with pytest.raises(SafeHttpError):
        await module.fetch_feed(
            FeedRequest(params={"lat": 1.0, "lon": 2.0}, refresh_s=1800), cast(SafeHttp, Boom())
        )


# --- content.rss ----------------------------------------------------------------

RSS_ITEM = {"url": "https://example.org/feed.xml"}


async def rss_fetch(name: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    module, _ = await ready("content.rss", values)
    fake = FakeHttp((FIX / name).read_bytes())
    req = FeedRequest(params={"url": "https://example.org/feed.xml"}, refresh_s=900)
    return await module.fetch_feed(req, http(fake))


async def test_rss2_text_only() -> None:
    data = await rss_fetch("rss2.xml")
    assert data["title"] == "Le Journal & Co"
    first, second, third = data["items"]
    assert first["title"] == "Premier titre"
    assert first["summary"] == "Un résumé avec lien et des entités & é."
    assert "<" not in first["summary"] and "alert" not in first["summary"]
    assert first["published"] == "2026-11-03T09:30:00Z"
    assert second["title"] == "Titre CDATA avec balises"
    assert second["summary"] == "Texte CDATA propre" and second["published"] is None
    assert third == {"title": "Troisième", "summary": "", "published": None}
    assert set(first) == {"title", "summary", "published"}  # ni lien ni image


async def test_atom() -> None:
    data = await rss_fetch("atom.xml")
    assert data["title"] == "Flux Atom"
    a, b = data["items"]
    assert (a["title"], a["summary"], a["published"]) == (
        "Entrée un", "Résumé Atom", "2026-11-03T09:00:00Z"
    )  # fmt: skip
    assert (b["title"], b["summary"], b["published"]) == (
        "Entrée deux", "Contenu texte", "2026-11-02T06:00:00Z"
    )  # fmt: skip


@pytest.mark.parametrize("name", ["rss_billion_laughs.xml", "rss_xxe.xml"])
async def test_trapped_xml_refused(name: str) -> None:
    with pytest.raises(ContractError) as exc:
        await rss_fetch(name)
    assert exc.value.code == "content.rss.xml_refused"


@pytest.mark.parametrize("body", [b"<html><body>no</body></html>", b"not xml", b"<rss/>", b""])
async def test_not_a_feed(body: bytes) -> None:
    module, _ = await ready("content.rss")
    req = FeedRequest(params={"url": "https://example.org/f"}, refresh_s=900)
    with pytest.raises(ContractError) as exc:
        await module.fetch_feed(req, http(FakeHttp(body)))
    assert exc.value.code == "content.rss.invalid_feed"


async def test_entries_and_lengths_bounded() -> None:
    entries = "".join(
        f"<item><title>{'t' * 500}</title><description>{'d' * 900}</description></item>"
        for _ in range(30)
    )
    module, _ = await ready("content.rss")
    body = f"<rss version='2.0'><channel><title>c</title>{entries}</channel></rss>".encode()
    req = FeedRequest(params={"url": "https://example.org/f"}, refresh_s=900)
    data = await module.fetch_feed(req, http(FakeHttp(body)))
    assert len(data["items"]) == 20
    assert all(len(i["title"]) <= 200 and len(i["summary"]) <= 400 for i in data["items"])
    assert len(json.dumps(data, ensure_ascii=False).encode()) < 64 * 1024


async def test_rss_fetch_uses_config_limits() -> None:
    module, _ = await ready("content.rss", {"max_bytes": 50_000_000})
    fake = FakeHttp((FIX / "atom.xml").read_bytes())
    await module.fetch_feed(
        FeedRequest(params={"url": "https://example.org/f"}, refresh_s=900), http(fake)
    )
    assert fake.calls[0]["max_bytes"] == 2 * 1024 * 1024  # plafond dur
    assert "application/atom+xml" in fake.calls[0]["a"]


async def test_rss_to_player() -> None:
    module, ctx = await ready("content.rss", {"refresh_min": 1})
    spec = await module.to_player({**RSS_ITEM, "max_items": 4, "item_duration_s": 6}, {})
    assert spec.duration_ms == 24000 and spec.feeds == [spec.props["feed_id"]]
    assert ctx.core.feeds.requests[0].refresh_s == 300 and spec.props["display"] == "list"
    ticker = await module.to_player({**RSS_ITEM, "display": "ticker"}, {})
    assert ticker.duration_ms is None
    assert ticker.feeds == spec.feeds  # même URL, même flux


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"url": "http://example.org/f"}, "invalid_url"),
        ({"url": "https://u:p@example.org/f"}, "invalid_url"),
        ({"url": ""}, "invalid_url"),
        ({"max_items": 11}, "invalid_max_items"),
        ({"max_items": 0}, "invalid_max_items"),
        ({"display": "marquee"}, "invalid_display"),
        ({"item_duration_s": 1}, "invalid_duration"),
        ({"max_age_s": 1}, "invalid_max_age"),
        ({"show_date": "yes"}, "invalid_show_date"),
    ],
)
async def test_rss_rejects_invalid_item(over: dict[str, Any], code: str) -> None:
    module, ctx = await ready("content.rss")
    with pytest.raises(ContractError) as exc:
        await module.to_player({**RSS_ITEM, **over}, {})
    assert exc.value.code == "content.rss." + code and not ctx.core.feeds.requests


async def test_rss_allowed_hosts_and_own_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VDS_PUBLIC_ORIGIN", "https://vds.example.org")
    module, _ = await ready("content.rss", {"allowed_hosts": ["example.org"]})
    await module.to_player({"url": "https://news.example.org/f"}, {})
    for url, code in [
        ("https://evil.test/f", "host_not_allowed"),
        ("https://notexample.org/f", "host_not_allowed"),
    ]:
        with pytest.raises(ContractError) as exc:
            await module.to_player({"url": url}, {})
        assert exc.value.code == "content.rss." + code
    open_module, _ = await ready("content.rss")
    with pytest.raises(ContractError) as own:
        await open_module.to_player({"url": "https://vds.example.org/api/v1/x"}, {})
    assert own.value.code == "content.rss.same_origin"
    # l'URL est revérifiée au rafraîchissement (liste blanche modifiée entre-temps)
    with pytest.raises(ContractError):
        await module.fetch_feed(
            FeedRequest(params={"url": "https://evil.test/f"}, refresh_s=900), http(FakeHttp(b""))
        )
