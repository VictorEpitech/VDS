"""Modèle `onboarding_state` (miroir de 0011) : étapes ignorées et carte masquée."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, ForeignKey, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from vds_server.db.base import Base, tstz
from vds_server.db.models import Timestamps


class OnboardingState(Base, Timestamps):
    __tablename__ = "onboarding_state"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True
    )
    skipped_steps: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    card_hidden_at: Mapped[datetime | None] = tstz(nullable=True, now=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
