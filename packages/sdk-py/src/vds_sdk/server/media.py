"""Types et façade media du noyau VDS."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MediaKind = Literal["image", "video", "document"]


class MediaInfo(BaseModel):
    """Informations sur un média."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    kind: MediaKind
    mime: str
    bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    page_count: int | None = None
    variants: list[str] = Field(default_factory=list)


class MediaFacade(Protocol):
    """Façade d'accès aux médias du noyau."""

    async def get(self, media_id: UUID) -> MediaInfo:
        """Récupère les informations sur un média.

        Exige la permission `media.read`.
        """

    def open(self, media_id: UUID, variant: str = "original") -> AsyncIterator[bytes]:
        """Ouvre un média en lecture binaire.

        Exige la permission `media.read`.
        """

    async def create(
        self, *, kind: MediaKind, mime: str, filename: str, chunks: AsyncIterator[bytes]
    ) -> MediaInfo:
        """Crée un nouveau média à partir de données binaires.

        Exige la permission `media.write`.
        """


__all__ = ["MediaFacade", "MediaInfo", "MediaKind"]
