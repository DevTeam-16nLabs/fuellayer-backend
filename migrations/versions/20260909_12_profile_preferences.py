"""Versioned preferences, lossless snack timing and nutrition target history.

Revision ID: 20260909_12
Revises: e449764316ff
"""

import sqlalchemy as sa
from alembic import op

revision = "20260909_12"
down_revision = "e449764316ff"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("preferences_revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("profiles", sa.Column("current_age", sa.Integer(), nullable=True))
    op.add_column(
        "profiles", sa.Column("age_recorded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("profiles", sa.Column("legacy_shopping", sa.JSON(), nullable=True))
    op.add_column(
        "food_preferences",
        sa.Column("excluded_ingredients", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column("food_preferences", sa.Column("snack_slots", sa.JSON(), nullable=True))
    op.create_table(
        "preference_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"),
    )
    op.create_index("ix_preference_receipts_user_id", "preference_receipts", ["user_id"])
    op.create_table(
        "nutrition_target_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "revision"),
    )
    op.create_index("ix_nutrition_target_history_user_id", "nutrition_target_history", ["user_id"])


def downgrade():
    op.drop_table("nutrition_target_history")
    op.drop_table("preference_receipts")
    op.drop_column("food_preferences", "snack_slots")
    op.drop_column("food_preferences", "excluded_ingredients")
    op.drop_column("profiles", "legacy_shopping")
    op.drop_column("profiles", "age_recorded_at")
    op.drop_column("profiles", "current_age")
    op.drop_column("users", "preferences_revision")
