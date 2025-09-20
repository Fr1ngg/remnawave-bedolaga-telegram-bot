"""add sent notifications table"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision: str = '8fd1e338eb45'
down_revision: Union[str, None] = '3d9b35c6bd8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = 'sent_notifications'
UNIQUE_CONSTRAINT_NAME = 'uq_sent_notifications'
UNIQUE_CONSTRAINT_COLUMNS = ['user_id', 'subscription_id', 'notification_type', 'days_before']


def _table_exists(inspector: Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def _unique_constraint_exists(inspector: Inspector) -> bool:
    existing_constraints = {
        constraint['name'] for constraint in inspector.get_unique_constraints(TABLE_NAME)
    }
    return UNIQUE_CONSTRAINT_NAME in existing_constraints


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector):
        op.create_table(
            TABLE_NAME,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('subscriptions.id'), nullable=False),
            sa.Column('notification_type', sa.String(length=50), nullable=False),
            sa.Column('days_before', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint(*UNIQUE_CONSTRAINT_COLUMNS, name=UNIQUE_CONSTRAINT_NAME),
        )
    elif not _unique_constraint_exists(inspector):
        op.create_unique_constraint(
            UNIQUE_CONSTRAINT_NAME, TABLE_NAME, UNIQUE_CONSTRAINT_COLUMNS
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector):
        op.drop_table(TABLE_NAME)
