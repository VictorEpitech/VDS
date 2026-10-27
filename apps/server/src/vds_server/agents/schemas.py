"""Schémas d'entrée et de sortie des agents (distincts, Pydantic v2)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vds_server.provisioning.tickets import ARGS_KEY

AgentStatus = Literal["online", "offline", "unseen", "revoked"]


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class AgentOut(BaseModel):
    id: UUID
    site_id: UUID
    name: str
    token_prefix: str
    status: AgentStatus
    version: str | None
    last_seen_at: datetime | None
    last_heartbeat: dict[str, Any] | None
    created_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None


class AgentWithToken(BaseModel):
    """Le jeton n'est renvoyé qu'ici, une seule fois."""

    agent: AgentOut
    token: str


class CommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    screen_id: UUID | None = None
    args: dict[str, Any] = {}
    ttl_s: int = Field(300, ge=5, le=86400)


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    seq: int
    name: str
    screen_id: UUID | None
    args: dict[str, Any]
    status: str
    code: str | None
    result: dict[str, Any] | None
    created_at: datetime
    sent_at: datetime | None
    done_at: datetime | None
    expires_at: datetime

    @field_validator("args")
    @classmethod
    def _mask_ticket(cls, v: dict[str, Any]) -> dict[str, Any]:
        """M5 : le ticket chiffré n'a rien à faire dans la liste des commandes."""
        return {k: ("***" if k == ARGS_KEY else x) for k, x in v.items()}
