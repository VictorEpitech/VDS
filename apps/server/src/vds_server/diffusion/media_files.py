"""Octets des médias : envoi (type, taille, sha256) et lecture partielle."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import ContractError, Event, EventBus, Module
from vds_sdk.server.content import ContentType, MediaMeta, MediaRef
from vds_sdk.server.storage import StorageBackend, StoredObject

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Media, MediaVariant
from vds_server.diffusion.base import ServiceBase

CHUNK = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# mime -> (kind, extension, vérification des octets d'en-tête). Pas de SVG (script embarqué).
ALLOWED: dict[str, tuple[str, str]] = {
    "image/jpeg": ("image", "jpg"),
    "image/png": ("image", "png"),
    "image/gif": ("image", "gif"),
    "image/webp": ("image", "webp"),
    "video/mp4": ("video", "mp4"),
    "video/webm": ("video", "webm"),
    "application/pdf": ("document", "pdf"),
}


def sniff(head: bytes) -> str | None:
    """Type réel d'après les octets d'en-tête ; None si non reconnu ou non autorisé."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


VARIANT_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class _VariantStore:
    """MediaStore confié au module : écrit les variantes à côté de l'original."""

    def __init__(self, storage: StorageBackend, key: str) -> None:
        self._storage = storage
        self._dir = key.rsplit("/", 1)[0]
        self._original = key
        self.created: dict[str, StoredObject] = {}

    async def put_variant(
        self, name: str, chunks: AsyncIterator[bytes], *, mime: str
    ) -> StoredObject:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", name) or name == "original":
            raise ContractError("media.variant_invalid", name)
        key = f"{self._dir}/{name}.{VARIANT_EXT.get(mime, 'bin')}"
        stored = await self._storage.put(key, chunks, mime=mime)
        self.created[name] = stored
        return stored

    def read(self, name: str) -> AsyncIterator[bytes]:
        if name == "original":
            return self._storage.open(self._original)
        stored = self.created.get(name)
        if stored is None:
            raise ContractError("media.variant_missing", name)
        return self._storage.open(stored.key)


class Readable(Protocol):
    async def read(self, size: int = ..., /) -> bytes: ...


@dataclass(frozen=True)
class Upload:
    file: Readable
    filename: str | None
    declared_mime: str | None
    title: str | None
    expected_sha256: str | None


class MediaUploadService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        storage: StorageBackend,
        max_bytes: int,
        processor: Callable[[str], tuple[str, Module] | None] | None = None,
    ) -> None:
        super().__init__(session, bus, actor)
        self._processor = processor
        self._storage = storage
        self._max = max_bytes

    async def upload(self, up: Upload) -> Media:
        expected = (up.expected_sha256 or "").lower() or None
        if expected is not None and not SHA256_RE.match(expected):
            raise ApiError("media.sha256_invalid", 422)
        head = await up.file.read(CHUNK)
        if not head:
            raise ApiError("media.empty", 422)
        mime = sniff(head)
        if mime is None or mime not in ALLOWED:
            raise ApiError("media.type_invalid", 415)
        declared = (up.declared_mime or "").split(";")[0].strip().lower()
        if declared and declared not in ("application/octet-stream", mime):
            raise ApiError("media.type_mismatch", 415)
        kind, ext = ALLOWED[mime]
        media_id = uuid4()
        key = f"{media_id.hex[:2]}/{media_id}/original.{ext}"

        async def chunks() -> AsyncIterator[bytes]:
            total = len(head)
            if total > self._max:
                raise ApiError("media.too_large", 413)
            yield head
            while block := await up.file.read(CHUNK):
                total += len(block)
                if total > self._max:
                    raise ApiError("media.too_large", 413)
                yield block

        try:
            stored = await self._storage.put(key, chunks(), mime=mime)
        except ApiError:
            await self._storage.delete(key)
            raise
        if expected is not None and stored.sha256 != expected:
            await self._storage.delete(key)
            raise ApiError("media.sha256_mismatch", 422)
        filename = Path(up.filename).name[:255] if up.filename else None
        title = (up.title or "").strip()[:120] or (filename or str(media_id))[:120]
        status, error, meta, store = "ready", None, MediaMeta(), _VariantStore(self._storage, key)
        found = self._processor(mime) if self._processor else None
        if found is not None and isinstance(found[1], ContentType):
            ref = MediaRef(
                id=media_id,
                kind=kind,  # type: ignore[arg-type]
                mime=mime,
                bytes=stored.bytes,
                sha256=stored.sha256,
                storage_key=key,
            )
            try:
                meta = await found[1].process_media(ref, store)
            except ContractError as exc:
                status, error = "failed", exc.code
            except Exception:
                status, error = "failed", "media.processing_failed"
            if status == "failed":
                meta = MediaMeta()
                for v in store.created.values():
                    await self._storage.delete(v.key)
                store.created.clear()
        media = Media(
            id=media_id,
            width=meta.width,
            height=meta.height,
            duration_ms=meta.duration_ms,
            page_count=meta.page_count,
            error=error,
            kind=kind,
            title=title,
            original_filename=filename,
            mime=mime,
            bytes=stored.bytes,
            sha256=stored.sha256,
            storage_backend=self._storage_id(),
            storage_key=key,
            status=status,
            created_by=self._actor.user_id,
        )
        self._s.add(media)
        await self._s.flush()  # media avant media_variant (FK)
        self._s.add(
            MediaVariant(
                media_id=media_id,
                name="original",
                mime=mime,
                bytes=stored.bytes,
                sha256=stored.sha256,
                storage_key=key,
            )
        )
        for name, obj in store.created.items():
            self._s.add(
                MediaVariant(
                    media_id=media_id,
                    name=name,
                    mime=obj.mime,
                    bytes=obj.bytes,
                    sha256=obj.sha256,
                    storage_key=obj.key,
                )
            )
        self._audit(
            "media.uploaded",
            "media",
            media_id,
            "media_id",
            kind=kind,
            mime=mime,
            bytes=stored.bytes,
        )
        self._events.append(
            Event(
                name="media.ready" if status == "ready" else "media.failed",
                payload={"media_id": str(media_id)} | ({"error": error} if error else {}),
                source="core",
            )
        )
        try:
            await self._commit("media.conflict")
        except ApiError:
            for v in store.created.values():
                await self._storage.delete(v.key)
            await self._storage.delete(key)
            raise
        await self._s.refresh(media)
        return media

    def _storage_id(self) -> str:
        return getattr(self._storage, "module_id", None) or "storage.local"


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclus

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None | str:
    """None = pas de Range exploitable (réponse complète) ; 'invalid' = 416."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[6:].strip()
    if "," in spec:
        return None  # multi-plages non gérées : réponse complète autorisée par la RFC 9110
    m = re.fullmatch(r"(\d*)-(\d*)", spec)
    if not m or (m[1] == "" and m[2] == ""):
        return None
    if m[1] == "":  # suffixe : les n derniers octets
        n = int(m[2])
        if n == 0 or size == 0:
            return "invalid"
        return ByteRange(max(size - n, 0), size - 1)
    start = int(m[1])
    end = min(int(m[2]), size - 1) if m[2] else size - 1
    if start >= size or end < start:
        return "invalid"
    return ByteRange(start, end)


async def read_range(
    storage: StorageBackend, key: str, rng: ByteRange | None, size: int
) -> AsyncIterator[bytes]:
    start, end = (rng.start, rng.end) if rng else (0, size - 1)
    path = await storage.local_path(key)
    if path is not None:
        fh = await asyncio.to_thread(path.open, "rb")
        try:
            await asyncio.to_thread(fh.seek, start)
            left = end - start + 1
            while left > 0:
                block = await asyncio.to_thread(fh.read, min(CHUNK, left))
                if not block:
                    break
                left -= len(block)
                yield block
        finally:
            await asyncio.to_thread(fh.close)
        return
    pos = 0
    async for block in storage.open(key):
        nxt = pos + len(block)
        if nxt > start and pos <= end:
            yield block[max(start - pos, 0) : end - pos + 1]
        pos = nxt
        if pos > end:
            break


def media_uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ApiError("media.not_found", 404) from exc
