import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fuellayer.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    diary_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    preferences_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    clerk_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    onboarding_status: Mapped[str] = mapped_column(String(32), default="not_started")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    measurements: Mapped[list["BodyMeasurement"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    goal: Mapped["Goal | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    food_preferences: Mapped["FoodPreference | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    nutrition_target: Mapped["NutritionTarget | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    starter_plan: Mapped["StarterPlanRecord | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    grocery_list: Mapped["GroceryListRecord | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    onboarding_completion: Mapped["OnboardingCompletion | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    planning_profile: Mapped["PlanningProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    legacy_shopping: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    current_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    age_at_onboarding: Mapped[int] = mapped_column(Integer)
    height_cm: Mapped[float] = mapped_column(Float)
    equation_sex: Mapped[str] = mapped_column(String(32))
    locale: Mapped[str] = mapped_column(String(16), default="en")
    preferred_units: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="profile")


class BodyMeasurement(Base):
    __tablename__ = "body_measurements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    weight_kg: Mapped[float] = mapped_column(Float)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="measurements")


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    goal_type: Mapped[str] = mapped_column(String(48))
    goal_detail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    daily_movement: Mapped[str] = mapped_column(String(48))
    training_days: Mapped[int] = mapped_column(Integer)
    training_focus: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="goal")


class FoodPreference(Base):
    __tablename__ = "food_preferences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    excluded_ingredients: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    snack_slots: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    dietary_pattern: Mapped[str] = mapped_column(String(32))
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list)
    meals_per_day: Mapped[int] = mapped_column(Integer)
    include_breakfast: Mapped[bool]
    include_snacks: Mapped[bool]
    cooking_time: Mapped[str] = mapped_column(String(32))
    servings: Mapped[int] = mapped_column(Integer)
    shopping_cadence: Mapped[str] = mapped_column(String(32))

    user: Mapped[User] = relationship(back_populates="food_preferences")


class NutritionTarget(Base):
    __tablename__ = "nutrition_targets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    daily_energy_kcal: Mapped[int] = mapped_column(Integer)
    protein_g: Mapped[int] = mapped_column(Integer)
    carbohydrates_g: Mapped[int] = mapped_column(Integer)
    fat_g: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String(24))
    engine_version: Mapped[str] = mapped_column(String(64))
    calculation_input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="nutrition_target")


class StarterPlanRecord(Base):
    __tablename__ = "starter_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    preview_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="starter_plan")


class GroceryListRecord(Base):
    __tablename__ = "grocery_lists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    grouped_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    content_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="grocery_list")


class OnboardingCompletion(Base):
    __tablename__ = "onboarding_completions"
    __table_args__ = (UniqueConstraint("clerk_subject", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    clerk_subject: Mapped[str] = mapped_column(String(255), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[int] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="onboarding_completion")


class PlanningProfile(Base):
    __tablename__ = "planning_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    living_arrangement: Mapped[str] = mapped_column(String(32))
    household_size: Mapped[int] = mapped_column(Integer)
    meal_contexts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    shopping_cadence: Mapped[str] = mapped_column(String(32))
    location_status: Mapped[str] = mapped_column(String(32))
    location_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    area_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    preferred_place_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="planning_profile")


class ClerkWebhookEvent(Base):
    __tablename__ = "clerk_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    clerk_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PreferenceReceipt(Base):
    __tablename__ = "preference_receipts"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NutritionTargetHistory(Base):
    __tablename__ = "nutrition_target_history"
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    __table_args__ = (UniqueConstraint("user_id", "revision"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    target: Mapped[dict[str, Any]] = mapped_column(JSON)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
