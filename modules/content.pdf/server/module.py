"""content.pdf : pages rendues en JPEG avec pypdfium2. Ne dépend que de `vds_sdk`."""

import asyncio
import json
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, MediaMeta, MediaRef, MediaStore, PlayerItemSpec
from vds_sdk.server.media import MediaInfo

DEFAULT_WIDTH = 3840
DEFAULT_MAX_PAGES = 100
MAX_HEIGHT = 10000
DEFAULT_PAGE_MS = 8000
DEFAULT_TIMEOUT_S = 60
MEM_LIMIT_BYTES = 2 * 1024**3
WORKER = Path(__file__).with_name("render_worker.py")
EXIT_INVALID = 3
EXIT_TOO_MANY = 4


async def _write(chunks: AsyncIterator[bytes], path: Path) -> None:
    fh = await asyncio.to_thread(path.open, "wb")
    try:
        async for chunk in chunks:
            await asyncio.to_thread(fh.write, chunk)
    finally:
        await asyncio.to_thread(fh.close)


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _page_number(name: str) -> int:
    return int(name.removeprefix("page-"))


class ContentPdf(ContentType):
    def __init__(self) -> None:
        self._width = DEFAULT_WIDTH
        self._max_pages = DEFAULT_MAX_PAGES
        self._timeout_s: float = DEFAULT_TIMEOUT_S
        self._cpu_s = DEFAULT_TIMEOUT_S
        self._mem_bytes = MEM_LIMIT_BYTES
        self._lock = asyncio.Lock()

    async def setup(self, ctx: ModuleContext) -> None:
        cfg = await ctx.config.resolve()
        self._width = int(cfg.get("render_width") or DEFAULT_WIDTH)
        self._max_pages = int(cfg.get("max_pages") or DEFAULT_MAX_PAGES)

    async def process_media(self, media: MediaRef, store: MediaStore) -> MediaMeta:
        tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="vds-pdf-"))
        try:
            path = tmp / "original.pdf"
            out = tmp / "out"
            await asyncio.to_thread(out.mkdir)
            await _write(store.read("original"), path)
            async with self._lock:  # un seul rendu à la fois (mémoire)
                meta = await self._run_worker(path, out)
                for index in range(meta["count"]):
                    data = await asyncio.to_thread((out / f"page-{index + 1}.jpg").read_bytes)
                    await store.put_variant(f"page-{index + 1}", _once(data), mime="image/jpeg")
            return MediaMeta(width=meta["width"], height=meta["height"], page_count=meta["count"])
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def _run_worker(self, path: Path, out: Path) -> dict[str, int]:
        """pdfium dans un sous-processus jetable : rlimits, délai, environnement vidé."""
        args = (
            str(WORKER), str(path), str(out), str(self._width), str(self._max_pages),
            str(MAX_HEIGHT), str(self._mem_bytes), str(self._cpu_s),
        )  # fmt: skip
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
                cwd=str(out),
                close_fds=True,
                start_new_session=True,
            )  # fmt: skip
        except OSError as exc:
            raise ContractError("content.pdf.render_failed") from exc
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), self._timeout_s)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ContractError("content.pdf.timeout") from exc
        if proc.returncode == EXIT_INVALID:
            raise ContractError("content.pdf.invalid")
        if proc.returncode == EXIT_TOO_MANY:
            raise ContractError("content.pdf.too_many_pages")
        if proc.returncode != 0:
            raise ContractError("content.pdf.render_failed")
        try:
            data = json.loads(raw)
            meta = {k: int(data[k]) for k in ("count", "width", "height")}
        except (ValueError, KeyError, TypeError) as exc:
            raise ContractError("content.pdf.render_failed") from exc
        if not 0 < meta["count"] <= self._max_pages:
            raise ContractError("content.pdf.render_failed")
        return meta

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        media_id = UUID(str(props["media"]))
        info = media.get(media_id)
        if info is None:
            raise ContractError("content.media_missing", str(media_id))
        names = sorted((v for v in info.variants if v.startswith("page-")), key=_page_number)
        pages = [f"media/{media_id}/{name}" for name in names]
        page_ms = int(props.get("page_duration_ms") or DEFAULT_PAGE_MS)
        return PlayerItemSpec(
            props={"pages": pages, "page_count": len(pages), "page_duration_ms": page_ms},
            assets=pages,
            duration_ms=page_ms * len(pages) if pages else None,
        )
