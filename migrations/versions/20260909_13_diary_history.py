"""Account diary, immutable revision snapshots, retry receipts and target provenance.

Revision ID: 20260909_13
Revises: 20260909_12
"""

import sqlalchemy as sa
from alembic import op

revision = "20260909_13"
down_revision = "20260909_12"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("diary_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "nutrition_target_history",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "diary_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("entry_id", sa.String(160), nullable=False),
        sa.Column("diary_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100)),
        sa.Column("offset_minutes", sa.Integer()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("change_seq", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "entry_id"),
    )
    for column in ["user_id", "diary_date", "change_seq"]:
        op.create_index("ix_diary_entries_" + column, "diary_entries", [column])
    op.create_table(
        "diary_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("entry_id", sa.String(160), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "entry_id", "revision"),
    )
    op.create_index("ix_diary_revisions_user_id", "diary_revisions", ["user_id"])
    op.create_table(
        "diary_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"),
    )
    op.create_index("ix_diary_receipts_user_id", "diary_receipts", ["user_id"])
    op.create_table(
        "diary_days",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("diary_date", sa.Date(), primary_key=True),
        sa.Column("timezone", sa.String(100)),
    )


def downgrade():
    for name in ["diary_days", "diary_receipts", "diary_revisions", "diary_entries"]:
        op.drop_table(name)
    op.drop_column("nutrition_target_history", "verified")
    op.drop_column("users", "diary_revision")
