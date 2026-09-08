"""Add the public, source-attributed food catalogue."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_04"
down_revision: str | None = "20260905_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_foods",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name_fr", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_version", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("nutrients", sa.JSON(), nullable=False),
        sa.Column("nutrient_notes", sa.JSON(), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.create_index(
            "ix_catalogue_foods_search",
            "catalogue_foods",
            ["search_text"],
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        )


def downgrade() -> None:
    op.drop_table("catalogue_foods")
