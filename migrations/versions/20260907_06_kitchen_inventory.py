"""Account-owned home stock, independent of grocery requirements."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_06"
down_revision: str | None = "20260907_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kitchen_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("ingredient_key", sa.String(200), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(40), nullable=True),
        sa.Column("location", sa.String(20), nullable=False),
        sa.Column("date_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="kitchen_positive_quantity"),
        sa.CheckConstraint("quantity IS NULL OR unit IS NOT NULL", name="kitchen_quantity_unit"),
        sa.CheckConstraint(
            "location IN ('fridge', 'freezer', 'pantry', 'unassigned')", name="kitchen_location"
        ),
    )
    op.create_index("ix_kitchen_items_user_id", "kitchen_items", ["user_id"])


def downgrade() -> None:
    op.drop_table("kitchen_items")
