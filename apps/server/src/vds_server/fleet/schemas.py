from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ScreensSummaryOut(BaseModel):
    total: int
    by_status: dict[str, int]


class ToHandleOut(BaseModel):
    screens: int
    by_reason: dict[str, int]


class IncidentsSummaryOut(BaseModel):
    open: int
    unacknowledged: int
    by_severity: dict[str, int]
    by_kind: dict[str, int]


class AgentsSummaryOut(BaseModel):
    total: int
    online: int
    offline: int


class FleetSummaryOut(BaseModel):
    site_id: UUID | None
    generated_at: datetime
    screens: ScreensSummaryOut
    to_handle: ToHandleOut
    incidents: IncidentsSummaryOut
    agents: AgentsSummaryOut
