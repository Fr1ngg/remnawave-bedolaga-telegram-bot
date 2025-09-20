"""add_paid_price_to_subscription

Revision ID: 3d9b35c6bd8f
Revises: 
Create Date: 2025-08-23 08:17:00.563340

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


# revision identifiers, used by Alembic.
revision: str = '3d9b35c6bd8f'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "subscriptions", "paid_price_kopeks"):
        op.add_column(
            "subscriptions",
            sa.Column(
                "paid_price_kopeks",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )

def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "subscriptions", "paid_price_kopeks"):
        op.drop_column("subscriptions", "paid_price_kopeks")
