"""content.image : dimensions et vignette à l'envoi. Ne dépend que de `vds_sdk` et Pillow."""

import asyncio
import io
import tempfile
from collections.abc import AsyncIterator, Mapping
from typing import IO, Any
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, MediaMeta, MediaRef, MediaStore, PlayerItemSpec
from vds_sdk.server.media import MediaInfo

MAX_PIXELS = 100_000_000
DEFAULT_THUMB = 320
FITS = ("contain", "cover")


async def _spool(chunks: AsyncIterator[bytes]) -> IO[bytes]:
    fh: IO[bytes] = await asyncio.to_thread(tempfile.TemporaryFile)
    try:
        async for chunk in chunks:
            await asyncio.to_thread(fh.write, chunk)
        await asyncio.to_thread(fh.seek, 0)
    except BaseException:
        fh.close()
        raise
    return fh


def _inspect(fh: IO[bytes], thumb_size: int) -> tuple[int, int, bytes]:
    try:
        with Image.open(fh) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ContractError("content.image.too_large")
            img.draft("RGB", (thumb_size * 2, thumb_size * 2))
            thumb = img.convert("RGB")
            thumb.thumbnail((thumb_size, thumb_size))
            out = io.BytesIO()
            thumb.save(out, "JPEG", quality=80)
            return width, height, out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise ContractError("content.image.invalid") from exc


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


class ContentImage(ContentType):
    def __init__(self) -> None:
        self._thumb_size = DEFAULT_THUMB

    async def setup(self, ctx: ModuleContext) -> None:
        cfg = await ctx.config.resolve()
        self._thumb_size = int(cfg.get("thumb_size") or DEFAULT_THUMB)

    async def process_media(self, media: MediaRef, store: MediaStore) -> MediaMeta:
        fh = await _spool(store.read("original"))
        try:
            width, height, thumb = await asyncio.to_thread(_inspect, fh, self._thumb_size)
        finally:
            fh.close()
        await store.put_variant("thumb", _once(thumb), mime="image/jpeg")
        return MediaMeta(width=width, height=height)

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        media_id = UUID(str(props["media"]))
        if media_id not in media:
            raise ContractError("content.media_missing", str(media_id))
        src = f"media/{media_id}/original"
        fit = props.get("fit")
        return PlayerItemSpec(
            props={"src": src, "fit": fit if fit in FITS else "contain"}, assets=[src]
        )
