import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class MealReplacement(Base):
    """Durable request result and conditional undo receipt; never a diary entry."""

    __tablename__ = "meal_replacements"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="completed")
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
