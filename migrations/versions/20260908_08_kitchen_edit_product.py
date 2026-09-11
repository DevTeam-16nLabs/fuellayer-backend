"""Edit individual lots and reversibly finish/remove stock."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_08"
down_revision: str | None = "20260908_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("kitchen_positive_quantity", "kitchen_items", type_="check")
    op.drop_constraint("kitchen_quantity_unit", "kitchen_items", type_="check")
    op.create_check_constraint(
        "kitchen_nonnegative_quantity", "kitchen_items", "quantity IS NULL OR quantity >= 0"
    )
    op.add_column(
        "kitchen_items", sa.Column("status", sa.String(12), nullable=False, server_default="active")
    )
    op.add_column(
        "kitchen_items", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_check_constraint(
        "kitchen_status", "kitchen_items", "status IN ('active', 'finished', 'removed')"
    )
    op.create_table(
        "kitchen_mutations",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("kitchen_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_kitchen_mutations_item_id", "kitchen_mutations", ["item_id"])


def downgrade() -> None:
    # Do not silently destroy zero/independent optional values or resurrect retired stock.
    connection = op.get_bind()
    incompatible = connection.scalar(
        sa.text(
            "SELECT count(*) FROM kitchen_items WHERE status != 'active' OR quantity = 0 "
            "OR (quantity IS NOT NULL AND unit IS NULL)"
        )
    )
    if incompatible:
        raise RuntimeError("Resolve stock incompatible with the prior schema before downgrade.")
    op.drop_table("kitchen_mutations")
    op.drop_constraint("kitchen_status", "kitchen_items", type_="check")
    op.drop_column("kitchen_items", "version")
    op.drop_column("kitchen_items", "status")
    op.drop_constraint("kitchen_nonnegative_quantity", "kitchen_items", type_="check")
    op.create_check_constraint(
        "kitchen_positive_quantity", "kitchen_items", "quantity IS NULL OR quantity > 0"
    )
    op.create_check_constraint(
        "kitchen_quantity_unit", "kitchen_items", "quantity IS NULL OR unit IS NOT NULL"
    )
