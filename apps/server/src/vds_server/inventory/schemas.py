"""Schémas d'entrée et de sortie distincts (Pydantic v2)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Orientation = Literal["landscape", "portrait"]
NAME = Field(min_length=1, max_length=120)
SLUG_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SLUG = Field(min_length=1, max_length=63, pattern=SLUG_RE)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrganizationOut(_Out):
    id: UUID
    name: str


class OrganizationPatch(_In):
    name: str = NAME


class SiteCreate(_In):
    name: str = NAME
    slug: str = SLUG


class SitePatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=63, pattern=SLUG_RE)
    archived: bool | None = None


class SiteOut(_Out):
    id: UUID
    name: str
    slug: str
    archived_at: datetime | None


class GroupCreate(_In):
    name: str = NAME


class GroupPatch(_In):
    name: str = NAME


class GroupOut(_Out):
    id: UUID
    site_id: UUID
    name: str


class ScreenCreate(_In):
    site_id: UUID
    group_id: UUID | None = None
    name: str = NAME
    orientation: Orientation = "landscape"


class ScreenPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    orientation: Orientation | None = None
    group_id: UUID | None = None  # présent (même null) = changer de groupe
    archived: bool | None = None


class ScreenOut(_Out):
    id: UUID
    site_id: UUID
    group_id: UUID | None
    name: str
    orientation: Orientation
    archived_at: datetime | None
