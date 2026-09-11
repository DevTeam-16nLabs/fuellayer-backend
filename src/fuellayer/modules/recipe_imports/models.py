import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class ImportedRecipe(Base):
    __tablename__ = "imported_recipes"
    __table_args__ = (UniqueConstraint("id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    nutrition_choice: Mapped[str] = mapped_column(String(16), default="source")
    library_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecipeIngredientRow(Base):
    __tablename__ = "imported_recipe_ingredients"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recipe_id", "user_id"],
            ["imported_recipes.id", "imported_recipes.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("recipe_id", "position"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID] = mapped_column()
    position: Mapped[int] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class RecipeStepRow(Base):
    __tablename__ = "imported_recipe_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recipe_id", "user_id"],
            ["imported_recipes.id", "imported_recipes.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("recipe_id", "position"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID] = mapped_column()
    position: Mapped[int] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class RecipeNutritionRow(Base):
    __tablename__ = "imported_recipe_nutrition"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recipe_id", "user_id"],
            ["imported_recipes.id", "imported_recipes.user_id"],
            ondelete="CASCADE",
        ),
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ImportJob(Base):
    __tablename__ = "recipe_import_jobs"
    __table_args__ = (Index("ix_recipe_jobs_due", "state", "next_attempt_at", "lease_until"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imported_recipes.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(32), default="queued")
    version: Mapped[int] = mapped_column(default=1)
    input: Mapped[dict[str, Any]] = mapped_column(JSON)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(default=0)
    lease: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ImportCommand(Base):
    __tablename__ = "recipe_import_commands"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ImportSource(Base):
    __tablename__ = "recipe_import_sources"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipe_import_jobs.id", ondelete="CASCADE")
    )
