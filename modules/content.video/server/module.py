"""content.video : durée et dimensions via ffprobe. Ne dépend que de `vds_sdk`."""

import asyncio
import json
import math
import resource
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, MediaMeta, MediaRef, MediaStore, PlayerItemSpec
from vds_sdk.server.media import MediaInfo

DEFAULT_TIMEOUT_S = 30
MEM_LIMIT_BYTES = 2 * 1024**3
FITS = ("contain", "cover")
THUMB_SIDE = 320
THUMB_MAX_BYTES = 512 * 1024
THUMB_TIMEOUT_S = 20


async def _write(chunks: AsyncIterator[bytes], path: Path) -> None:
    fh = await asyncio.to_thread(path.open, "wb")
    try:
        async for chunk in chunks:
            await asyncio.to_thread(fh.write, chunk)
    finally:
        await asyncio.to_thread(fh.close)


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _limit_child(cpu_s: int) -> None:
    """Exécuté dans l'enfant avant exec : plafonds mémoire, CPU et taille de fichier."""
    resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def parse_probe(raw: bytes) -> MediaMeta:
    """Extrait durée et dimensions d'affichage de la sortie JSON de ffprobe."""
    try:
        data = json.loads(raw)
        streams = data["streams"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ContractError("content.video.invalid") from exc
    video = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None
    )
    if video is None:
        raise ContractError("content.video.invalid")
    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ContractError("content.video.invalid")
    rotation = 0
    for side in video.get("side_data_list") or []:
        if isinstance(side, dict) and isinstance(side.get("rotation"), int | float):
            rotation = abs(int(side["rotation"]))
    if rotation % 180 == 90:
        width, height = height, width
    fmt = data.get("format") or {}
    seconds = _seconds(fmt.get("duration")) or _seconds(video.get("duration"))
    return MediaMeta(
        width=width, height=height, duration_ms=None if seconds is None else round(seconds * 1000)
    )


class ContentVideo(ContentType):
    ffprobe_bin = "ffprobe"
    ffmpeg_bin = "ffmpeg"

    def __init__(self) -> None:
        self._timeout_s = DEFAULT_TIMEOUT_S

    async def setup(self, ctx: ModuleContext) -> None:
        cfg = await ctx.config.resolve()
        self._timeout_s = int(cfg.get("probe_timeout_s") or DEFAULT_TIMEOUT_S)

    async def _probe(self, path: Path) -> bytes:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffprobe_bin,
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"},
                cwd=str(path.parent),
                close_fds=True,
                start_new_session=True,
                preexec_fn=lambda: _limit_child(max(1, math.ceil(self._timeout_s)) + 5),  # noqa: PLW1509
            )  # fmt: skip
        except OSError as exc:
            raise ContractError("content.video.probe_unavailable") from exc
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout_s)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ContractError("content.video.probe_timeout") from exc
        if proc.returncode != 0:
            raise ContractError("content.video.invalid")
        return out

    async def _thumb(self, path: Path, duration_ms: int | None) -> bytes | None:
        """Image à 10 % de la durée en WebP (même sandbox que ffprobe). None si indisponible."""
        at = 0.0 if duration_ms is None else duration_ms / 1000 * 0.1
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg_bin,
                "-v", "error", "-nostdin",
                "-ss", f"{at:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-vf", f"scale=w={THUMB_SIDE}:h={THUMB_SIDE}:force_original_aspect_ratio=decrease",
                "-c:v", "libwebp", "-q:v", "60",
                "-f", "webp", "pipe:1",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"},
                cwd=str(path.parent),
                close_fds=True,
                start_new_session=True,
                preexec_fn=lambda: _limit_child(THUMB_TIMEOUT_S + 5),  # noqa: PLW1509
            )  # fmt: skip
        except OSError:
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), THUMB_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0 or not out or len(out) > THUMB_MAX_BYTES:
            return None
        return out

    async def process_media(self, media: MediaRef, store: MediaStore) -> MediaMeta:
        tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="vds-video-"))
        try:
            path = tmp / "original"
            await _write(store.read("original"), path)
            meta = parse_probe(await self._probe(path))
            thumb = await self._thumb(path, meta.duration_ms)
            if thumb is not None:  # vignette facultative : absente = icône du kind
                await store.put_variant("thumb", _once(thumb), mime="image/webp")
            return meta
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        media_id = UUID(str(props["media"]))
        info = media.get(media_id)
        if info is None:
            raise ContractError("content.media_missing", str(media_id))
        src = f"media/{media_id}/original"
        fit = props.get("fit")
        return PlayerItemSpec(
            props={
                "src": src,
                "audio": props.get("audio") is True,
                "fit": fit if fit in FITS else "contain",
                "known_duration_ms": info.duration_ms,
            },
            assets=[src],
            duration_ms=None,
        )
