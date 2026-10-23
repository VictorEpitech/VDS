"""Envoi des octets d'un média et service `<base>/media/<id>/<variant>`."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.server.storage import StorageBackend

from vds_server.config.reader import ModuleConfigReader
from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models import MediaVariant
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.media_files import (
    ByteRange,
    MediaUploadService,
    Upload,
    media_uuid,
    parse_range,
    read_range,
)

VARIANT_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["media-files"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Editor = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    def storage() -> StorageBackend:
        backend = rt.storage_backend()
        if backend is None:
            raise ApiError("media.storage_unavailable", 503)
        return backend

    Storage = Annotated[StorageBackend, Depends(storage)]

    @router.post("/api/v1/media", response_model=sc.MediaOut, status_code=201)
    async def upload_media(
        request: Request,
        session: Session,
        actor: Editor,
        backend: Storage,
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form(max_length=120)] = None,
        sha256: Annotated[str | None, Form(max_length=64)] = None,
    ) -> sc.MediaOut:
        cfg = await ModuleConfigReader(rt.maker, rt.schemas, CORE_NAMESPACE).resolve()
        max_bytes = int(cfg.get("media_max_upload_mb", 2048)) * 1024 * 1024
        svc = MediaUploadService(
            session, rt.bus, actor, backend, max_bytes, rt.registry.content_processor
        )
        media = await svc.upload(
            Upload(
                file=file,
                filename=file.filename,
                declared_mime=file.content_type,
                title=title,
                expected_sha256=sha256,
            )
        )
        return sc.MediaOut.model_validate(media)

    @router.head("/media/{media_id}/{variant}", include_in_schema=False)
    @router.get("/media/{media_id}/{variant}")
    async def serve_media(
        request: Request, media_id: str, variant: str, session: Session, backend: Storage
    ) -> Response:
        await rt.media_access.authorize(request)
        if not VARIANT_RE.match(variant):
            raise ApiError("media.not_found", 404)
        row = (
            await session.execute(
                select(MediaVariant).where(
                    MediaVariant.media_id == media_uuid(media_id), MediaVariant.name == variant
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("media.not_found", 404)
        etag = f'"{row.sha256}"' if row.sha256 else None
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        }
        if etag:
            headers["ETag"] = etag
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
        size = row.bytes
        parsed = parse_range(request.headers.get("range"), size)
        if parsed == "invalid":
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )
        rng = parsed if isinstance(parsed, ByteRange) else None
        status = 206 if rng else 200
        length = rng.length if rng else size
        if rng:
            headers["Content-Range"] = f"bytes {rng.start}-{rng.end}/{size}"
        headers["Content-Length"] = str(length)
        if request.method == "HEAD":
            return Response(status_code=status, media_type=row.mime, headers=headers)
        return StreamingResponse(
            read_range(backend, row.storage_key, rng, size),
            status_code=status,
            media_type=row.mime,
            headers=headers,
        )

    return router
