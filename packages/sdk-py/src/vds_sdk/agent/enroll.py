"""Enrôlement d'agent par code à usage unique (contrat vds-core 1.4.0).

Modèles partagés serveur / agent : requête et réponse d'enrôlement, dépôt local de code
(`enroll.json`), présence de l'agent local (`presence.json`) et payloads des événements.
Le code n'est jamais journalisé : `SecretStr` partout où il circule.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints

ENROLL_CODE_PREFIX = "vds_enr_"
ENROLL_CODE_PATTERN = r"^vds_enr_[0-9A-HJKMNP-TV-Z]{26}$"
PRESENCE_MAX_BYTES = 4096

EnrollCode = Annotated[str, StringConstraints(pattern=ENROLL_CODE_PATTERN)]
Hostname = Annotated[str, StringConstraints(max_length=253, pattern=r"^[A-Za-z0-9._-]+$")]
EnrollChannel = Literal["remote", "local"]


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: EnrollCode
    hostname: Hostname
    agent_version: Annotated[str, StringConstraints(max_length=32)]
    platform: Annotated[str, StringConstraints(max_length=32)]  # "linux/amd64", "linux/arm64"
    previous_token: SecretStr | None = None  # agent déjà enrôlé qui change de site


class EnrollResponse(BaseModel):
    agent_id: UUID
    site_id: UUID
    token: SecretStr  # vds_agt_…, remis une seule fois
    ws_path: Literal["ws/agent"] = "ws/agent"


class LocalHandoff(BaseModel):
    """Fichier `<dir>/enroll.json`, écrit par le serveur, lu puis supprimé par l'agent."""

    v: Literal[1] = 1
    code: SecretStr
    enrollment_id: UUID
    expires_at: datetime


class LocalPresence(BaseModel):
    """Fichier `<dir>/presence.json`, écrit par l'agent (≤ 4 Kio) : donnée non fiable."""

    model_config = ConfigDict(extra="ignore")

    v: Literal[1] = 1
    hostname: Annotated[str, StringConstraints(max_length=253)]
    agent_version: Annotated[str, StringConstraints(max_length=32)]
    platform: Annotated[str, StringConstraints(max_length=32)]
    state: Literal["dormant", "enrolling", "active", "error"]
    agent_id: UUID | None = None
    error: Annotated[str, StringConstraints(max_length=128)] | None = None  # code i18n
    ts: datetime


def parse_presence(raw: bytes) -> LocalPresence:
    """Valide un `presence.json` brut ; ValueError si trop gros ou invalide."""
    if len(raw) > PRESENCE_MAX_BYTES:
        raise ValueError("enroll.presence_too_large")
    return LocalPresence.model_validate_json(raw)


# --- payloads d'événements (versionnés @1) -------------------------------------------


class AgentEnrollmentCreated(BaseModel):
    enrollment_id: UUID
    site_id: UUID
    channel: EnrollChannel
    expires_at: datetime
    created_by: UUID | None


class AgentEnrollmentRevoked(BaseModel):
    enrollment_id: UUID
    site_id: UUID
    reason: Literal["admin", "superseded"]


class AgentEnrolled(BaseModel):
    agent_id: UUID
    site_id: UUID
    enrollment_id: UUID
    channel: EnrollChannel
    hostname: str
    platform: str
    agent_version: str
    replaced: bool
    moved_from_site_id: UUID | None = None
