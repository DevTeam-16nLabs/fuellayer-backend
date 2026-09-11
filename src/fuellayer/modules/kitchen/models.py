import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class KitchenItem(Base):
    """One stock lot; never inferred from planned meals or grocery requirements."""

    __tablename__ = "kitchen_items"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="kitchen_request_per_user"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="kitchen_nonnegative_quantity"),
        CheckConstraint("status IN ('active', 'finished', 'removed')", name="kitchen_status"),
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
    category: Mapped[str] = mapped_column(
        String(80), default="Non classé", server_default="Non classé"
    )
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    # Optional explicit link to an ingredient name in the recipe catalogue.
    # Never fuzzy-match a branded/raw/cooked food to infer its identity.
    ingredient_key: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    location: Mapped[str] = mapped_column(String(20), default="unassigned")
    date_on: Mapped[date | None] = mapped_column(Date)
    # Storage history is separate from the legacy deadline displayed by Kitchen.
    storage_date: Mapped[date | None] = mapped_column(Date)
    food_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("catalogue_foods.id", ondelete="SET NULL")
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column()
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KitchenMutation(Base):
    """Account-scoped receipts make uncertain writes safe to retry, even after undo."""

    __tablename__ = "kitchen_mutations"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kitchen_items.id", ondelete="CASCADE"), index=True
    )
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
