"""Durable single-meal replacement and undo receipts."""

import sqlalchemy as sa
from alembic import op

revision = "20260908_10"
down_revision = "20260908_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_replacements",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM meal_replacements")):
        raise RuntimeError("Export replacement history before removing its storage.")
    op.drop_table("meal_replacements")
