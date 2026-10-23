from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..module import Module
from .media import MediaInfo, MediaKind
from .storage import StoredObject

__all__ = ["ContentType", "MediaMeta", "MediaRef", "MediaStore", "PlayerItemSpec"]


class MediaRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    kind: MediaKind
    mime: str
    bytes: int
    sha256: str
    storage_key: str


class MediaMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    page_count: int | None = None


class MediaStore(Protocol):
    async def put_variant(
        self, name: str, chunks: AsyncIterator[bytes], *, mime: str
    ) -> StoredObject: ...

    def read(self, name: str) -> AsyncIterator[bytes]: ...


class PlayerItemSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    props: dict[str, Any]
    assets: list[str] = Field(default_factory=list)
    duration_ms: int | None = None


class ContentType(Module):
    """
    Classe de base abstraite pour les types de contenu.

    Les méthodes `process_media` et `to_player` peuvent être surchargées
    pour personnaliser le comportement du type de contenu.
    """

    async def process_media(self, media: MediaRef, store: MediaStore) -> MediaMeta:
        """
        Traite un média après son envoi.

        Cette méthode est appelée lorsque le média est accepté par `content.media.accepts`.

        Args:
            media: Référence au média envoyé.
            store: Stockage pour sauvegarder les variantes du média.

        Returns:
            Métadonnées du média traité.
        """
        return MediaMeta()

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        """
        Convertit les propriétés et les médias en spécification pour le lecteur.

        Args:
            props: Propriétés du contenu.
            media: Médias associés au contenu.

        Returns:
            Spécification pour le lecteur.
        """
        return PlayerItemSpec(props=dict(props))
