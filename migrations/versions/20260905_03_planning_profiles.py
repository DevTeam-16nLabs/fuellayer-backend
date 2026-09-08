"""Add onboarding v2 planning profiles.

Revision ID: 20260905_03
Revises: 20260826_02
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_03"
down_revision: str | None = "20260826_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("living_arrangement", sa.String(length=32), nullable=False),
        sa.Column("household_size", sa.Integer(), nullable=False),
        sa.Column("meal_contexts", sa.JSON(), nullable=False),
        sa.Column("shopping_cadence", sa.String(length=32), nullable=False),
        sa.Column("location_status", sa.String(length=32), nullable=False),
        sa.Column("location_source", sa.String(length=32), nullable=True),
        sa.Column("area_label", sa.String(length=160), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("preferred_place_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_planning_profiles_user_id", "planning_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_table("planning_profiles")
