"""Schémas du planning admin (`/api/v1/planning`) : timeline, instant, simulation.

Entrées (`_In`) et sorties distinctes. Aucune chaîne destinée à l'utilisateur : codes stables.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from vds_server.diffusion.priority import PriorityLevel
from vds_server.diffusion.resolver import ReasonCode
from vds_server.diffusion.schemas import ScheduleCreate, SchedulePatch, TargetType, _In

MAX_WINDOW_DAYS = 42
MAX_SIMULATE_DAYS = 14
MAX_SCREENS_PER_PAGE = 100
DEFAULT_SCREENS_PER_PAGE = 50

SegmentState = Literal["play", "off", "scheduled_off", "overridden"]


class ContentRef(BaseModel):
    kind: Literal["playlist", "layout"]
    id: UUID
    name: str


class SourceRef(BaseModel):
    """Provenance d'un segment."""

    kind: Literal["schedule", "override", "power_plan", "none", "draft"]
    id: UUID | None = None
    name: str | None = None
    target_type: TargetType | None = None
    target_id: UUID | None = None
    target_name: str | None = None
    inherited: bool = False  # cible != écran affiché
    priority: int | None = None
    priority_level: PriorityLevel | None = None


class Loser(BaseModel):
    source: SourceRef
    content: ContentRef | None
    reason: ReasonCode  # pourquoi il ne joue pas sur ce segment


class Segment(BaseModel):
    start: datetime  # UTC ; l'UI affiche dans `timezone`
    end: datetime
    state: SegmentState
    content: ContentRef | None
    source: SourceRef
    reason: ReasonCode | None  # pourquoi ce gagnant ; None si explain=false
    losers: list[Loser] = []  # vide si explain=false


class Marker(BaseModel):
    at: datetime
    kind: Literal[
        "playlist_expires", "media_expires", "override_ends", "schedule_starts", "schedule_ends"
    ]
    ref_id: UUID
    name: str | None
    impact: Literal["gap", "fallback", "none"]


class Conflict(BaseModel):
    kind: Literal[
        "never_plays",
        "partially_masked",
        "ambiguous_tie",
        "content_expires",
        "content_empty",
        "gap",
    ]
    severity: Literal["info", "warning"]
    source: SourceRef
    other: SourceRef | None
    first_at: datetime
    occurrences: int


class ScreenTimeline(BaseModel):
    screen_id: UUID
    screen_name: str
    timezone: str
    segments: list[Segment]
    markers: list[Marker]
    conflicts: list[Conflict]  # calculés seulement avec explain=true


class Lane(BaseModel):
    """Candidat applicable à la cible (vue « couches »)."""

    source: SourceRef
    content: ContentRef | None
    occurrences: list[tuple[datetime, datetime]]  # éligibilité avant arbitrage (UTC)
    effective_ratio: float  # part de ses occurrences où il gagne sur au moins un écran


class TimelineOut(BaseModel):
    format: Literal[1] = 1
    target_type: TargetType
    target_id: UUID
    window: tuple[datetime, datetime]
    timezone: str  # fuseau du premier écran de la cible (par défaut : Europe/Paris)
    mixed_timezones: bool
    lanes: list[Lane]
    screens: list[ScreenTimeline]  # page courante
    next_cursor: str | None
    computed_at: datetime


class AtScreen(BaseModel):
    screen_id: UUID
    screen_name: str
    segment: Segment  # explain=true
    next_change_at: datetime | None
    presence: str  # état de supervision de l'écran (online, offline, ...)


class AtOut(BaseModel):
    at: datetime
    screens: list[AtScreen]
    next_cursor: str | None


class SimulateIn(_In):
    draft: ScheduleCreate | None = None  # nouvelle programmation
    edit: tuple[UUID, SchedulePatch] | None = None  # modification d'une existante
    remove: UUID | None = None  # « que se passe-t-il si je supprime ? »
    target_type: TargetType  # vue à simuler (souvent la cible du brouillon)
    target_id: UUID
    from_: date = Field(alias="from")
    to: date  # ≤ 14 jours

    @model_validator(mode="after")
    def _one_change(self) -> "SimulateIn":
        if sum(x is not None for x in (self.draft, self.edit, self.remove)) != 1:
            raise ValueError("planning.simulate_one_change")
        return self


class ChangeOut(BaseModel):
    screen_id: UUID
    start: datetime
    end: datetime
    before: Segment
    after: Segment


class SimulateOut(BaseModel):
    after: TimelineOut  # explain=true, sources « draft » marquées
    changes: list[ChangeOut]
    new_conflicts: list[Conflict]
    resolved_conflicts: list[Conflict]
    screens_touched: int
