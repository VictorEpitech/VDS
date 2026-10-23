"""Schémas d'entrée et de sortie distincts (Pydantic v2) pour le planning d'alimentation."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vds_server.config.api import OriginOut
from vds_server.diffusion.schemas import NAME, TIME_RE, RuleIn, RuleOut, TargetType, to_minutes

PowerState = Literal["on", "off"]
CALENDAR_KEY = r"^[a-z0-9]+(\.[a-z0-9]+(-[a-z0-9]+)*)*$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- plans -------------------------------------------------------------------
class PlanCreate(_In):
    name: str = NAME
    description: str | None = Field(default=None, max_length=2000)
    rules: list[RuleIn] = Field(default_factory=list, max_length=50)


class PlanPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: list[RuleIn] | None = Field(default=None, max_length=50)


class PlanOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    rules: list[RuleOut]
    created_at: datetime
    updated_at: datetime


# --- exceptions --------------------------------------------------------------
class _ExceptionBase(_In):
    scope_level: TargetType
    scope_id: UUID | None = None  # organisation : absent = toute l'organisation
    starts_on: date
    ends_on: date
    start: str | None = Field(default=None, pattern=TIME_RE.pattern)  # absent = journée entière
    end: str | None = Field(default=None, pattern=TIME_RE.pattern)
    state: PowerState
    label: str = NAME

    @model_validator(mode="after")
    def _valid(self) -> "_ExceptionBase":
        if self.starts_on > self.ends_on:
            raise ValueError("power.exception.period_invalid")
        if (self.start is None) != (self.end is None):
            raise ValueError("power.exception.time_incomplete")
        if self.start is not None and self.end is not None:
            if to_minutes(self.start) >= to_minutes(self.end):
                raise ValueError("power.exception.range_invalid")
        if self.scope_level != "organization" and self.scope_id is None:
            raise ValueError("power.exception.scope_required")
        return self


class ExceptionCreate(_ExceptionBase):
    pass


class ExceptionReplace(_ExceptionBase):
    """PUT : remplacement complet (évite les ambiguïtés de patch sur les champs nullables)."""


class ExceptionOut(BaseModel):
    id: UUID
    scope_level: TargetType
    scope_id: UUID | None
    starts_on: date
    ends_on: date
    start: str | None
    end: str | None
    state: PowerState
    label: str
    created_at: datetime
    updated_at: datetime


# --- calendriers -------------------------------------------------------------
class CalendarCreate(_In):
    key: str = Field(pattern=CALENDAR_KEY, max_length=80)
    name: dict[str, str] = Field(min_length=1)  # locale -> libellé

    @model_validator(mode="after")
    def _names(self) -> "CalendarCreate":
        if any(not k or not v or len(v) > 120 for k, v in self.name.items()):
            raise ValueError("power.calendar.name_invalid")
        return self


class CalendarOut(_Out):
    key: str
    module_id: str
    name: dict[str, Any]
    refreshed_at: datetime | None
    day_count: int


class CalendarDayIn(_In):
    label: str = NAME


class CalendarDayOut(_Out):
    day: date
    label: str


# --- plan résolu d'un écran --------------------------------------------------
class SlotOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    state: PowerState


class ScreenPowerOut(BaseModel):
    screen_id: UUID
    timezone: str
    plan_id: UUID | None
    plan_id_origin: OriginOut
    calendars: list[str]
    calendars_origin: OriginOut
    slots: list[SlotOut]
