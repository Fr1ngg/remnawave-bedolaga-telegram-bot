"""Add trial promocode parameters"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd21e72b9c3ab'
down_revision: Union[str, None] = '8fd1e338eb45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROMOCODES_TABLE = 'promocodes'
SUBSCRIPTIONS_TABLE = 'subscriptions'


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column['name'] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, PROMOCODES_TABLE, 'subscription_traffic_gb'):
        op.add_column(
            PROMOCODES_TABLE,
            sa.Column('subscription_traffic_gb', sa.Integer(), nullable=True)
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, PROMOCODES_TABLE, 'subscription_device_limit'):
        op.add_column(
            PROMOCODES_TABLE,
            sa.Column('subscription_device_limit', sa.Integer(), nullable=True)
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, PROMOCODES_TABLE, 'subscription_squads'):
        op.add_column(
            PROMOCODES_TABLE,
            sa.Column('subscription_squads', sa.JSON(), nullable=True)
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, PROMOCODES_TABLE, 'traffic_reset_strategy'):
        op.add_column(
            PROMOCODES_TABLE,
            sa.Column('traffic_reset_strategy', sa.String(length=20), nullable=True)
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, SUBSCRIPTIONS_TABLE, 'traffic_reset_strategy'):
        op.add_column(
            SUBSCRIPTIONS_TABLE,
            sa.Column('traffic_reset_strategy', sa.String(length=20), nullable=True)
        )
        op.execute(
            sa.text(
                "UPDATE subscriptions SET traffic_reset_strategy = 'MONTH' "
                "WHERE traffic_reset_strategy IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, SUBSCRIPTIONS_TABLE, 'traffic_reset_strategy'):
        op.drop_column(SUBSCRIPTIONS_TABLE, 'traffic_reset_strategy')

    inspector = sa.inspect(bind)
    if _column_exists(inspector, PROMOCODES_TABLE, 'traffic_reset_strategy'):
        op.drop_column(PROMOCODES_TABLE, 'traffic_reset_strategy')

    inspector = sa.inspect(bind)
    if _column_exists(inspector, PROMOCODES_TABLE, 'subscription_squads'):
        op.drop_column(PROMOCODES_TABLE, 'subscription_squads')

    inspector = sa.inspect(bind)
    if _column_exists(inspector, PROMOCODES_TABLE, 'subscription_device_limit'):
        op.drop_column(PROMOCODES_TABLE, 'subscription_device_limit')

    inspector = sa.inspect(bind)
    if _column_exists(inspector, PROMOCODES_TABLE, 'subscription_traffic_gb'):
        op.drop_column(PROMOCODES_TABLE, 'subscription_traffic_gb')
