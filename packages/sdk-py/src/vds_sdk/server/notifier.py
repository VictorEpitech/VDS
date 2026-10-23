"""Interface `Notifier` (cible server), contrat vds-core 1.2.0."""

from abc import abstractmethod
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints

from ..module import Module

__all__ = ["DeliveryResult", "Notification", "Notifier", "Recipient"]


class Recipient(BaseModel):
    # EmailStr évité : pas de dépendance email-validator dans le SDK
    email: Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    locale: Literal["fr", "en"] = "fr"


class Notification(BaseModel):
    id: UUID  # incident + transition : clé d'idempotence
    kind: str
    transition: Literal["opened", "resolved"]
    severity: Literal["info", "warning", "critical"]
    subject: dict[str, str]  # noms, jamais d'id technique
    occurred_at: datetime
    link: str  # chemin relatif à l'admin


class DeliveryResult(BaseModel):
    ok: bool
    code: str | None = None
    retry_after_s: int | None = None


class Notifier(Module):
    @abstractmethod
    async def send(self, n: Notification, recipients: list[Recipient]) -> DeliveryResult: ...
