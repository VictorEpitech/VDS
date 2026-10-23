"""storage.local : médias sur disque. Ne dépend que de `vds_sdk`."""

import asyncio
import hashlib
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from vds_sdk import ModuleContext
from vds_sdk.server.storage import StorageBackend, StoredObject

CHUNK = 1024 * 1024
_KEY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f-]{36}/[a-z0-9-]{1,40}\.[a-z0-9]{1,8}$")
TMP_DIR = ".tmp"


class StorageLocal(StorageBackend):
    def __init__(self) -> None:
        self._root: Path | None = None

    async def setup(self, ctx: ModuleContext) -> None:
        cfg = await ctx.config.resolve()
        raw = str(cfg.get("media_dir") or "/data/media")
        root = await asyncio.to_thread(lambda: Path(raw).resolve())
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        self._root = root

    def _path(self, key: str) -> Path:
        if self._root is None:
            raise RuntimeError("storage.not_ready")
        if not _KEY_RE.match(key):
            raise ValueError("storage.key_invalid")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("storage.key_invalid")
        return path

    async def put(self, key: str, chunks: AsyncIterator[bytes], *, mime: str) -> StoredObject:
        target = self._path(key)
        assert self._root is not None  # noqa: S101 - garanti par _path
        tmp_dir = self._root / TMP_DIR
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
        tmp = tmp_dir / uuid.uuid4().hex
        digest = hashlib.sha256()
        size = 0
        fh: Any = await asyncio.to_thread(tmp.open, "wb")
        try:
            async for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                await asyncio.to_thread(fh.write, chunk)
            await asyncio.to_thread(fh.flush)
            await asyncio.to_thread(os.fsync, fh.fileno())
        except BaseException:
            await asyncio.to_thread(fh.close)
            await asyncio.to_thread(tmp.unlink, missing_ok=True)
            raise
        await asyncio.to_thread(fh.close)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, tmp, target)
        return StoredObject(key=key, bytes=size, sha256=digest.hexdigest(), mime=mime)

    async def open(self, key: str) -> AsyncIterator[bytes]:  # type: ignore[override]
        path = self._path(key)
        fh: Any = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(fh.read, CHUNK):
                yield chunk
        finally:
            await asyncio.to_thread(fh.close)

    async def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if await asyncio.to_thread(path.is_file) else None

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        parent = path.parent
        try:  # retire le dossier du média s'il est vide
            await asyncio.to_thread(parent.rmdir)
        except OSError:
            pass
