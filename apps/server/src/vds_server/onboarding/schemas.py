from datetime import datetime
from typing import Literal

from pydantic import BaseModel

StepId = Literal["account", "site", "screen", "media", "playlist", "schedule", "power"]
StepStatus = Literal["todo", "in_progress", "done", "skipped"]


class StepOut(BaseModel):
    step: StepId
    status: StepStatus
    optional: bool
    skippable: bool
    count: int
    details: dict[str, int | bool]


class ProgressOut(BaseModel):
    completed: int
    total: int


class CardOut(BaseModel):
    hidden: bool
    hidden_at: datetime | None


class OnboardingOut(BaseModel):
    steps: list[StepOut]
    progress: ProgressOut
    next_step: StepId | None
    complete: bool
    card: CardOut


class StepSkipIn(BaseModel):
    skipped: bool


class CardIn(BaseModel):
    hidden: bool
