"""Autorisation de lecture des médias : cookie de session ou jeton d'appareil (point d'accroche).

Le jeton d'appareil (`vds_dev_…`) est livré par l'appairage (lot 2 T4) : il enregistre ici un
lecteur via `register_reader`. Sans lecteur, seule la session par cookie est acceptée.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request

from vds_server.core.auth import AuthPipeline

# Retourne True si la requête porte un jeton d'appareil valide ; False sinon (jamais d'exception
# pour un jeton absent ou inconnu : on retombe sur la session).
DeviceReader = Callable[[Request], Awaitable[bool]]


class MediaAccess:
    def __init__(self, pipeline: AuthPipeline) -> None:
        self._pipeline = pipeline
        self._readers: list[DeviceReader] = []

    def register_reader(self, reader: DeviceReader) -> None:
        self._readers.append(reader)

    async def authorize(self, request: Request) -> None:
        """Lève ApiError 401/403 si ni un appareil ni un utilisateur connecté."""
        for reader in self._readers:
            if await reader(request):
                return
        await self._pipeline.principal(request)
