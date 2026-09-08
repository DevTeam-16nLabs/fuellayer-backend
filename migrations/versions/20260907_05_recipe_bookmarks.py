"""Save catalogue recipes independently from plans and consumed meals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_05"
down_revision: str | None = "20260906_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipe_bookmarks",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("recipe_id", sa.String(100), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("recipe_bookmarks")
