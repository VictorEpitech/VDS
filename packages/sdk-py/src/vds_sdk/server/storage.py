from abc import abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..module import Module

__all__ = ["StorageBackend", "StoredObject"]


class StoredObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    bytes: int
    sha256: str
    mime: str


class StorageBackend(Module):
    """
    Interface du backend de stockage pour le noyau VDS.
    """

    @abstractmethod
    async def put(self, key: str, chunks: AsyncIterator[bytes], *, mime: str) -> StoredObject:
        """
        Stocke un objet à partir d'un itérateur de chunks et retourne ses métadonnées.
        """

    @abstractmethod
    def open(self, key: str) -> AsyncIterator[bytes]:
        """
        Retourne un itérateur asynchrone pour lire les données de l'objet.
        """

    @abstractmethod
    async def local_path(self, key: str) -> Path | None:
        """
        Retourne le chemin local de l'objet si disponible, sinon None.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """
        Supprime l'objet correspondant à la clé donnée.
        """
