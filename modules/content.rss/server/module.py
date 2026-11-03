"""content.rss : titres d'un flux RSS 2.0 / Atom, réduits à du texte brut côté serveur.

Le serveur récupère (SafeHttp du noyau) et normalise ; le player ne contacte jamais le flux.
Analyse XML par `defusedxml` (DTD, entités et références externes refusées). Aucune image, aucun
lien, aucun HTML ne sort du serveur. Ne dépend que de `vds_sdk` et `defusedxml`.
"""

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, PlayerItemSpec
from vds_sdk.server.feeds import FeedRequest
from vds_sdk.server.http import SafeHttp
from vds_sdk.server.media import MediaInfo

DEFAULT_REFRESH_MIN = 15
MIN_REFRESH_MIN = 5
DEFAULT_MAX_BYTES = 1024 * 1024
HARD_MAX_BYTES = 2 * 1024 * 1024
KEPT_ENTRIES = 20
TITLE_MAX = 200
SUMMARY_MAX = 400
ACCEPT = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
_SPACES = re.compile(r"\s+")


class _Text(HTMLParser):
    """Réduit du HTML à son texte ; le contenu de script/style est ignoré."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(raw: str | None, limit: int) -> str:
    """Texte brut : balises retirées, entités décodées, espaces réduits, borné à `limit`."""
    text = raw or ""
    # deux passes : le HTML d'un flux est souvent échappé (&lt;p&gt;) dans le XML, la première
    # passe le décode, la seconde retire les balises ainsi révélées
    for _ in range(2):
        parser = _Text()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    text = _SPACES.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _date(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    when: datetime | None = None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _child_text(node: Element, *names: str) -> str | None:
    for child in node:
        if _local(child.tag) in names:
            return "".join(child.itertext())
    return None


def parse_feed(body: bytes) -> dict[str, Any]:
    """RSS 2.0 ou Atom -> `{title, items: [{title, summary, published}]}` (20 entrées au plus)."""
    try:
        root = SafeET.fromstring(body, forbid_dtd=True)
    except DefusedXmlException as exc:
        raise ContractError("content.rss.xml_refused") from exc
    except Exception as exc:  # XML mal formé
        raise ContractError("content.rss.invalid_feed") from exc
    kind = _local(root.tag)
    if kind == "rss":
        container = next((c for c in root if _local(c.tag) == "channel"), None)
        entries = [
            c for c in (container if container is not None else []) if _local(c.tag) == "item"
        ]
    elif kind == "feed":
        container = root
        entries = [c for c in root if _local(c.tag) == "entry"]
    else:
        raise ContractError("content.rss.invalid_feed")
    if container is None:
        raise ContractError("content.rss.invalid_feed")
    items: list[dict[str, Any]] = []
    for entry in entries:
        title = plain_text(_child_text(entry, "title"), TITLE_MAX)
        summary = plain_text(
            _child_text(entry, "description", "summary", "content", "encoded"), SUMMARY_MAX
        )
        if not title and not summary:
            continue
        published = _date(_child_text(entry, "pubDate", "published", "updated", "date"))
        items.append({"title": title, "summary": summary, "published": published})
        if len(items) >= KEPT_ENTRIES:
            break
    return {"title": plain_text(_child_text(container, "title"), TITLE_MAX), "items": items}


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.hostname:
            return None
        port = parts.port or {"https": 443, "http": 80}.get(parts.scheme.lower(), 0)
        return (parts.scheme.lower(), parts.hostname.lower().rstrip("."), port)
    except ValueError:
        return None


def _host(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError(url)
        parts.port  # noqa: B018 - lève ValueError si le port est invalide
        return parts.hostname.lower()
    except ValueError as exc:
        raise ContractError("content.rss.invalid_url") from exc


def _int(value: object, default: int, lo: int, hi: int, code: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ContractError(code)
    return value


class ContentRss(ContentType):
    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._allowed: frozenset[str] = frozenset()
        self._refresh_s = DEFAULT_REFRESH_MIN * 60
        self._max_bytes = DEFAULT_MAX_BYTES
        self._own_origin: tuple[str, str, int] | None = None

    async def setup(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        self._own_origin = _origin(os.environ.get("VDS_PUBLIC_ORIGIN", ""))
        await self._read_config()

    async def _read_config(self) -> None:
        if self._ctx is None:
            raise ContractError("content.rss.not_ready")
        cfg = await self._ctx.config.resolve()
        self._allowed = frozenset(str(h).strip().lower() for h in cfg.get("allowed_hosts") or [])
        minutes = cfg.get("refresh_min")
        minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None
        self._refresh_s = max(minutes or DEFAULT_REFRESH_MIN, MIN_REFRESH_MIN) * 60
        limit = cfg.get("max_bytes")
        limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
        self._max_bytes = min(limit or DEFAULT_MAX_BYTES, HARD_MAX_BYTES)

    def check_url(self, url: str) -> str:
        """Mêmes règles que content.url : https, liste blanche, jamais l'origine du VDS."""
        host = _host(url)
        if self._own_origin is not None and _origin(url) == self._own_origin:
            raise ContractError("content.rss.same_origin", host)
        if self._allowed and not any(host == a or host.endswith("." + a) for a in self._allowed):
            raise ContractError("content.rss.host_not_allowed", host)
        return url

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        if self._ctx is None:
            raise ContractError("content.rss.not_ready")
        url = self.check_url(str(props.get("url") or "").strip())
        max_items = _int(props.get("max_items"), 5, 1, 10, "content.rss.invalid_max_items")
        step = _int(props.get("item_duration_s"), 8, 2, 120, "content.rss.invalid_duration")
        max_age = _int(props.get("max_age_s"), 86400, 600, 604800, "content.rss.invalid_max_age")
        display = props.get("display", "list")
        if display not in ("list", "ticker"):
            raise ContractError("content.rss.invalid_display")
        show_date = props.get("show_date", True)
        if not isinstance(show_date, bool):
            raise ContractError("content.rss.invalid_show_date")
        feed_id = await self._ctx.core.feeds.ensure(
            FeedRequest(params={"url": url}, refresh_s=self._refresh_s)
        )
        return PlayerItemSpec(
            props={
                "display": display,
                "max_items": max_items,
                "item_duration_s": step,
                "show_date": show_date,
                "max_age_s": max_age,
                "feed_id": feed_id,
            },
            assets=[],
            duration_ms=max_items * step * 1000 if display == "list" else None,
            feeds=[feed_id],
        )

    async def fetch_feed(self, req: FeedRequest, http: SafeHttp) -> dict[str, Any]:
        await self._read_config()
        url = self.check_url(str(req.params.get("url") or ""))
        resp = await http.get(url, max_bytes=self._max_bytes, accept=ACCEPT)
        return parse_feed(resp.body)
