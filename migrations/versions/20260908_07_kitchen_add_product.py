"""Separate storage dates and idempotent product creation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_07"
down_revision: str | None = "20260907_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kitchen_items", sa.Column("storage_date", sa.Date(), nullable=True))
    op.add_column("kitchen_items", sa.Column("food_id", sa.String(100), nullable=True))
    op.add_column("kitchen_items", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.add_column("kitchen_items", sa.Column("request_hash", sa.String(64), nullable=True))
    op.create_foreign_key(
        "kitchen_catalogue_food",
        "kitchen_items",
        "catalogue_foods",
        ["food_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "kitchen_request_per_user", "kitchen_items", ["user_id", "request_id"]
    )


def downgrade() -> None:
    op.drop_constraint("kitchen_request_per_user", "kitchen_items", type_="unique")
    op.drop_constraint("kitchen_catalogue_food", "kitchen_items", type_="foreignkey")
    for column in ["request_hash", "request_id", "food_id", "storage_date"]:
        op.drop_column("kitchen_items", column)
