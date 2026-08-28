"""Add onboarding v1 domain.

Revision ID: 20260826_01
Revises:
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clerk_subject", sa.String(length=255), nullable=False),
        sa.Column("onboarding_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_subject"),
    )
    op.create_index("ix_users_clerk_subject", "users", ["clerk_subject"])

    op.create_table(
        "account_deletion_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clerk_subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_subject"),
    )
    op.create_index(
        "ix_account_deletion_requests_clerk_subject",
        "account_deletion_requests",
        ["clerk_subject"],
    )

    op.create_table(
        "clerk_webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_clerk_webhook_events_event_id", "clerk_webhook_events", ["event_id"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("age_at_onboarding", sa.Integer(), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=False),
        sa.Column("equation_sex", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("preferred_units", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    op.create_table(
        "body_measurements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_body_measurements_user_id", "body_measurements", ["user_id"])

    op.create_table(
        "goals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("goal_type", sa.String(length=48), nullable=False),
        sa.Column("goal_detail", sa.String(length=32), nullable=True),
        sa.Column("daily_movement", sa.String(length=48), nullable=False),
        sa.Column("training_days", sa.Integer(), nullable=False),
        sa.Column("training_focus", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_goals_user_id", "goals", ["user_id"])

    op.create_table(
        "food_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("dietary_pattern", sa.String(length=32), nullable=False),
        sa.Column("allergens", sa.JSON(), nullable=False),
        sa.Column("meals_per_day", sa.Integer(), nullable=False),
        sa.Column("include_breakfast", sa.Boolean(), nullable=False),
        sa.Column("include_snacks", sa.Boolean(), nullable=False),
        sa.Column("cooking_time", sa.String(length=32), nullable=False),
        sa.Column("servings", sa.Integer(), nullable=False),
        sa.Column("shopping_cadence", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_food_preferences_user_id", "food_preferences", ["user_id"])

    op.create_table(
        "nutrition_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("daily_energy_kcal", sa.Integer(), nullable=False),
        sa.Column("protein_g", sa.Integer(), nullable=False),
        sa.Column("carbohydrates_g", sa.Integer(), nullable=False),
        sa.Column("fat_g", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("calculation_input_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_nutrition_targets_user_id", "nutrition_targets", ["user_id"])

    op.create_table(
        "starter_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("preview_payload", sa.JSON(), nullable=False),
        sa.Column("content_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_starter_plans_user_id", "starter_plans", ["user_id"])

    op.create_table(
        "onboarding_completions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("clerk_subject", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_subject", "idempotency_key"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_onboarding_completions_clerk_subject",
        "onboarding_completions",
        ["clerk_subject"],
    )
    op.create_index("ix_onboarding_completions_user_id", "onboarding_completions", ["user_id"])


def downgrade() -> None:
    op.drop_table("onboarding_completions")
    op.drop_table("starter_plans")
    op.drop_table("nutrition_targets")
    op.drop_table("food_preferences")
    op.drop_table("goals")
    op.drop_table("body_measurements")
    op.drop_table("profiles")
    op.drop_table("clerk_webhook_events")
    op.drop_table("account_deletion_requests")
    op.drop_table("users")
