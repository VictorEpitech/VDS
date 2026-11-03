"""Schémas distincts entrée/sortie des flux et payloads du bus (version 1)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class FeedFailedV1(BaseModel):
    version: Literal[1] = 1
    module_id: str
    feed_id: str
    error_code: str
    failing_since: datetime


class FeedRecoveredV1(BaseModel):
    version: Literal[1] = 1
    module_id: str
    feed_id: str
    fetched_at: datetime


class FeedOut(BaseModel):
    """Réponse de `GET /api/v1/player/feeds/{feed_id}` (données déjà normalisées par le module)."""

    feed_id: str
    fetched_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)
