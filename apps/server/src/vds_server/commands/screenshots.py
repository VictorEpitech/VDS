"""Captures d'écran (lot I) : validation stricte, réencodage, stockage, purge.

L'image reçue est une donnée non fiable : octets magiques, taille, dimensions avant décodage,
réencodage Pillow (supprime EXIF, ICC et tout bloc annexe), jamais de nom de fichier client.
La clé de stockage est fabriquée par le serveur (`screenshots/<aa>/<screen_id>/<id>.jpg`), jamais
servie par `/media`.
"""

import asyncio
import hashlib
import io
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Request
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.server import StorageBackend

from vds_server.commands import schemas as sc
from vds_server.commands.service import PENDING, terminate
from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core.errors import ApiError
from vds_server.db.models import Organization
from vds_server.db.models_command import PlayerCommand, Screenshot

log = logging.getLogger("vds.screenshots")

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000  # contrôlé sur l'en-tête, avant tout décodage (bombe de décompression)
MAX_EDGE = 1280
JPEG_QUALITY = 70
PERIODIC_MIN_INTERVAL_S = 60
FUTURE_SKEW = timedelta(minutes=5)
PURGE_BATCH = 500
DEFAULT_RETENTION_H = 24
DEFAULT_MAX_PER_SCREEN = 10
JPEG_MAGIC = b"\xff\xd8\xff"

StorageGetter = Callable[[], StorageBackend | None]


async def read_body(request: Request, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Corps brut `image/jpeg`, plafonné en flux (413 au-delà), type déclaré contrôlé (415)."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "image/jpeg":
        raise ApiError("screenshot.unsupported_type", 415)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError("screenshot.too_large", 413, max_bytes=limit)
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ApiError("screenshot.too_large", 413, max_bytes=limit)
    return bytes(buf)


def parse_captured_at(raw: str | None, received: datetime) -> datetime:
    if raw is None or not raw.strip():
        return received
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ApiError("screenshot.captured_at_invalid", 422) from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return received if value > received + FUTURE_SKEW else value


def reencode(data: bytes) -> tuple[bytes, int, int]:
    """JPEG entrant -> JPEG sans métadonnées, plus grand côté <= 1280 px, qualité 70."""
    if not data.startswith(JPEG_MAGIC):
        raise ApiError("screenshot.unsupported_type", 415)
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as img:
            width, height = img.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ApiError("screenshot.too_many_pixels", 413, max_pixels=MAX_PIXELS)
            img.draft("RGB", (MAX_EDGE, MAX_EDGE))  # décodage réduit : mémoire bornée
            rgb = img.convert("RGB")
        rgb.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except ApiError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise ApiError("screenshot.invalid_image", 415) from exc
    blob = out.getvalue()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ApiError("screenshot.too_large", 413, max_bytes=MAX_UPLOAD_BYTES)
    return blob, rgb.width, rgb.height


async def _chunks(blob: bytes) -> AsyncIterator[bytes]:
    yield blob


class ScreenshotIngest:
    """Réception d'une capture par le player authentifié (à la demande ou périodique)."""

    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        storage: StorageGetter,
        interval_of: Callable[[UUID], Awaitable[int]],
        gate: asyncio.Semaphore,
    ) -> None:
        self._s = session
        self._bus = bus
        self._storage = storage
        self._interval_of = interval_of
        self._gate = gate

    async def _command(self, cmd_id: UUID, device_id: UUID, *, lock: bool) -> PlayerCommand:
        stmt = select(PlayerCommand).where(
            PlayerCommand.id == cmd_id, PlayerCommand.device_id == device_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if (
            row is None
            or row.name != "screenshot"
            or row.status not in PENDING
            or row.expires_at <= datetime.now(UTC)
        ):
            raise ApiError("screenshot.command_invalid", 409)
        return row

    async def _rate_check(self, screen_id: UUID) -> None:
        last = await self._s.scalar(
            select(func.max(Screenshot.received_at)).where(
                Screenshot.screen_id == screen_id, Screenshot.trigger == "periodic"
            )
        )
        if last is not None:
            wait = PERIODIC_MIN_INTERVAL_S - (datetime.now(UTC) - last).total_seconds()
            if wait > 0:
                raise ApiError("screenshot.rate_limited", 429, retry_after_s=int(wait) + 1)

    async def ingest(
        self,
        device_id: UUID,
        screen_id: UUID,
        cmd_id: UUID | None,
        data: bytes,
        captured_header: str | None,
    ) -> UUID:
        storage = self._storage()
        if storage is None:
            raise ApiError("screenshot.storage_unavailable", 503)
        received = datetime.now(UTC)
        captured = parse_captured_at(captured_header, received)
        if cmd_id is not None:
            await self._command(cmd_id, device_id, lock=False)
        else:
            if await self._interval_of(screen_id) <= 0:
                raise ApiError("screenshot.disabled", 409)
            await self._rate_check(screen_id)
        async with self._gate:
            blob, width, height = await asyncio.to_thread(reencode, data)

        shot_id = uuid4()
        key = f"screenshots/{screen_id.hex[:2]}/{screen_id}/{shot_id}.jpg"
        stored = await storage.put(key, _chunks(blob), mime="image/jpeg")
        events: list[Event] = []
        try:
            await self._s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"pc:{screen_id}"},
            )
            if cmd_id is not None:
                cmd = await self._command(cmd_id, device_id, lock=True)
            else:
                await self._rate_check(screen_id)
                cmd = None
            shot = Screenshot(
                id=shot_id,
                screen_id=screen_id,
                command_id=cmd.id if cmd is not None else None,
                trigger="on_demand" if cmd is not None else "periodic",
                captured_at=captured,
                received_at=received,
                width=width,
                height=height,
                bytes=len(blob),
                sha256=stored.sha256 or hashlib.sha256(blob).hexdigest(),
                storage_key=key,
            )
            self._s.add(shot)
            await self._s.flush()
            if cmd is not None:
                events.append(
                    terminate(self._s, cmd, "ok", None, result={"screenshot_id": str(shot_id)})
                )
            events.append(
                Event(
                    name="screen.screenshot_captured",
                    payload=sc.ScreenScreenshotCapturedV1(
                        screenshot_id=shot_id,
                        screen_id=screen_id,
                        trigger=shot.trigger,  # type: ignore[arg-type]
                        captured_at=captured,
                    ).model_dump(mode="json"),
                    source="core",
                )
            )
            await self._s.commit()
        except BaseException:
            await self._s.rollback()
            await storage.delete(key)
            raise
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)
        return shot_id


async def read_image(storage: StorageBackend | None, row: Screenshot) -> bytes:
    if storage is None:
        raise ApiError("screenshot.not_found", 404)
    try:
        return b"".join([chunk async for chunk in storage.open(row.storage_key)])
    except Exception as exc:  # fichier absent : la ligne est orpheline, purgée plus tard
        raise ApiError("screenshot.not_found", 404) from exc


async def screenshot_keys(session: AsyncSession, screen_id: UUID) -> list[str]:
    rows = await session.execute(
        select(Screenshot.storage_key).where(Screenshot.screen_id == screen_id)
    )
    return list(rows.scalars())


async def delete_files(storage: StorageBackend | None, keys: list[str]) -> None:
    if storage is None:
        return
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:
            log.warning("screenshot.file_delete_failed")


class CaptureSweeper:
    """Expiration des commandes échues (chaque passage) et purge des captures (15 min)."""

    PURGE_EVERY_S = 15 * 60

    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        schemas: ConfigSchemaProvider,
        storage: StorageGetter,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._schemas = schemas
        self._storage = storage
        self._last_purge = float("-inf")

    async def expire(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        events: list[Event] = []
        async with self._maker() as s:
            rows = (
                (
                    await s.execute(
                        select(PlayerCommand)
                        .where(PlayerCommand.status.in_(PENDING), PlayerCommand.expires_at <= now)
                        .order_by(PlayerCommand.expires_at)
                        .limit(PURGE_BATCH)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                events.append(terminate(s, row, "expired", "cmd.expired", now=now))
            await s.commit()
        for event in events:
            await self._bus.publish(event)
        return len(events)

    async def limits(self, s: AsyncSession) -> tuple[int, int]:
        org = await s.scalar(select(Organization.id))
        if org is None:
            return DEFAULT_RETENTION_H, DEFAULT_MAX_PER_SCREEN
        res = await ConfigService(s, self._schemas).resolve("organization", org, CORE_NAMESPACE)
        return (
            int(res.values["screenshots.retention_h"].value),
            int(res.values["screenshots.max_per_screen"].value),
        )

    async def purge(self, now: datetime | None = None) -> int:
        """Supprime le fichier PUIS la ligne (au-delà de la rétention ou du plafond par écran)."""
        now = now or datetime.now(UTC)
        storage = self._storage()
        if storage is None:
            return 0
        async with self._maker() as s:
            retention_h, max_per = await self.limits(s)
            ranked = select(
                Screenshot.id,
                func.row_number()
                .over(partition_by=Screenshot.screen_id, order_by=Screenshot.captured_at.desc())
                .label("rn"),
            ).subquery()
            rows = (
                (
                    await s.execute(
                        select(Screenshot)
                        .where(
                            or_(
                                Screenshot.received_at < now - timedelta(hours=retention_h),
                                Screenshot.id.in_(select(ranked.c.id).where(ranked.c.rn > max_per)),
                            )
                        )
                        .limit(PURGE_BATCH)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            done = 0
            for row in rows:
                try:
                    await storage.delete(row.storage_key)
                except Exception:
                    log.warning("screenshot.purge_file_failed")
                    continue  # la ligne reste : nouvel essai au prochain passage
                await s.delete(row)
                done += 1
            await s.commit()
            return done

    async def run_once(self) -> None:
        try:
            await self.expire()
            loop_time = asyncio.get_running_loop().time()
            if loop_time - self._last_purge >= self.PURGE_EVERY_S:
                self._last_purge = loop_time
                await self.purge()
        except Exception:
            log.exception("commands.sweep_failed")
