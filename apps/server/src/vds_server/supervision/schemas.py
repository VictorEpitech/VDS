"""Schémas d'API des incidents (sortie seule ; l'acquittement n'a pas de corps)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class IncidentOut(BaseModel):
    id: UUID
    kind: str
    severity: str
    subject_type: str
    subject_id: str
    subject_name: str | None
    site_id: UUID | None
    site_name: str | None
    details: dict[str, Any]
    opened_at: datetime
    resolved_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: UUID | None
