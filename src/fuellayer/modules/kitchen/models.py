import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class KitchenItem(Base):
    """One stock lot; never inferred from planned meals or grocery requirements."""

    __tablename__ = "kitchen_items"
    __table_args__ = (
        CheckConstraint("quantity IS NULL OR quantity > 0", name="kitchen_positive_quantity"),
        CheckConstraint("quantity IS NULL OR unit IS NOT NULL", name="kitchen_quantity_unit"),
        CheckConstraint(
            "location IN ('fridge', 'freezer', 'pantry', 'unassigned')",
            name="kitchen_location",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # Optional explicit link to an ingredient name in the recipe catalogue.
    # Never fuzzy-match a branded/raw/cooked food to infer its identity.
    ingredient_key: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    location: Mapped[str] = mapped_column(String(20), default="unassigned")
    date_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
