"""Façade `FeedsApi` (cible server), contrat vds-core 1.12.0 : flux de données mis en cache."""

import hashlib
import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

__all__ = ["FeedRequest", "FeedsApi", "compute_feed_id"]


def compute_feed_id(module_id: str, request: dict[str, Any]) -> str:
    """sha256(module_id + JSON canonique trié)[:32]."""
    canon = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((module_id + canon).encode()).hexdigest()[:32]


class FeedRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    params: dict[str, Any]
    refresh_s: int  # borné par le noyau à [300, 86400]

    def canonical(self) -> dict[str, Any]:
        """Partie identifiante (refresh_s exclu : changer la fréquence ne change pas le flux)."""
        return {"params": self.params}


class FeedsApi(Protocol):
    async def ensure(self, req: FeedRequest) -> str:
        """Crée ou touche le flux, renvoie feed_id. Exige la permission `content.feeds`."""
        ...
