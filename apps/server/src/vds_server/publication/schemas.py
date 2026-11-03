"""Schémas d'API et payloads d'événements de la validation de publication (vds-core 1.13.0)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EntityTypeLit = Literal["playlist", "layout", "schedule"]
StateLit = Literal["draft", "in_review", "published"]
COMMENT_MAX = 500


class UserRef(BaseModel):
    id: UUID
    display_name: str | None = None


class EntityRef(BaseModel):
    entity_type: EntityTypeLit
    entity_id: UUID


class PublicationOut(BaseModel):
    entity_type: EntityTypeLit
    entity_id: UUID
    state: StateLit
    working_rev: int
    published_rev: int | None
    submitted_by: UserRef | None = None
    submitted_at: datetime | None = None
    published_by: UserRef | None = None
    published_at: datetime | None = None
    review_comment: str | None = None
    # objets à publier d'abord (état « brouillon » seulement) ; vide dans les listes
    blocked_by: list[EntityRef] = Field(default_factory=list)
    name: str | None = None  # nom de l'objet (listes d'attente)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubmitIn(_In):
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)


class WithdrawIn(_In):
    pass


class ApproveIn(_In):
    expected_rev: int = Field(ge=0)
    include_dependencies: bool = False


class RejectIn(_In):
    comment: str = Field(min_length=1, max_length=COMMENT_MAX)


# --- événements (v=1) ---------------------------------------------------------
class _Event(BaseModel):
    v: Literal[1] = 1
    entity_type: EntityTypeLit
    entity_id: UUID
    rev: int


class PublicationSubmittedV1(_Event):
    actor_id: UUID | None
    comment: str | None


class PublicationWithdrawnV1(_Event):
    actor_id: UUID | None


class PublicationApprovedV1(_Event):
    actor_id: UUID | None
    author_id: UUID | None
    auto: bool


class PublicationRejectedV1(_Event):
    actor_id: UUID | None
    author_id: UUID | None
    comment: str
