"""Schémas d'entrée et de sortie de l'enrôlement (distincts, Pydantic v2)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from vds_sdk.agent.enroll import LocalPresence

EnrollmentState = Literal["pending", "consumed", "expired", "revoked"]
Channel = Literal["remote", "local"]


class EnrollmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_min: int = Field(default=60, ge=10, le=1440)


class EnrollmentCommands(BaseModel):
    docker_run: str
    docker_login: str | None = None


class EnrollmentFiles(BaseModel):
    agent_env: str


class EnrollmentCreated(BaseModel):
    id: UUID
    code: str  # remis une seule fois
    expires_at: datetime
    server_url: str
    image: str
    commands: EnrollmentCommands
    files: EnrollmentFiles


class EnrollmentOut(BaseModel):
    id: UUID
    channel: Channel
    created_at: datetime
    expires_at: datetime
    state: EnrollmentState
    consumed_ip: str | None
    consumed_hostname: str | None
    agent_id: UUID | None


class EnrollOut(BaseModel):
    """Réponse d'enrôlement : le jeton est en clair ici, une seule fois (cf. EnrollResponse)."""

    agent_id: UUID
    site_id: UUID
    token: str
    ws_path: Literal["ws/agent"] = "ws/agent"


class LocalAgentOut(BaseModel):
    """Canal local : `detected` est une donnée non fiable de l'agent (affichage seul)."""

    available: bool
    detected: LocalPresence | None
    site_id: UUID | None


class LocalActivated(BaseModel):
    enrollment_id: UUID
    expires_at: datetime
