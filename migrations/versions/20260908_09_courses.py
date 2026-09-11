"""Durable shopping purchases and receipt provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_09"
down_revision: str | None = "20260908_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kitchen_items",
        sa.Column("category", sa.String(80), nullable=False, server_default="Non classé"),
    )
    op.create_table(
        "courses_ledgers",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "courses_requests",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
    )
    op.create_table(
        "receipt_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("image", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease", sa.String(36)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_receipt_jobs_user_id", "receipt_jobs", ["user_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM courses_ledgers")):
        raise RuntimeError("Export purchase history before removing Courses storage.")
    op.drop_table("receipt_jobs")
    op.drop_table("courses_requests")
    op.drop_table("courses_ledgers")
    op.drop_column("kitchen_items", "category")
