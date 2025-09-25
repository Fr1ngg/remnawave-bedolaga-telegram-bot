"""add auto promo group history"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision: str = 'b3fd7e1811e4'
down_revision: Union[str, None] = '8fd1e338eb45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


USERS_TABLE = 'users'
PROMO_GROUPS_TABLE = 'promo_groups'
COLUMN_NAME = 'auto_promo_group_id'
FK_NAME = 'fk_users_auto_promo_group_id_promo_groups'


def _column_exists(inspector: Inspector) -> bool:
    return any(column['name'] == COLUMN_NAME for column in inspector.get_columns(USERS_TABLE))


def _foreign_key_exists(inspector: Inspector) -> bool:
    return any(fk['name'] == FK_NAME for fk in inspector.get_foreign_keys(USERS_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector):
        op.add_column(USERS_TABLE, sa.Column(COLUMN_NAME, sa.Integer(), nullable=True))

    inspector = sa.inspect(bind)

    if _column_exists(inspector) and not _foreign_key_exists(inspector):
        try:
            op.create_foreign_key(
                FK_NAME,
                USERS_TABLE,
                PROMO_GROUPS_TABLE,
                [COLUMN_NAME],
                ['id'],
                ondelete='SET NULL',
            )
        except Exception:
            pass

    dialect_name = bind.dialect.name
    if dialect_name == 'mysql':
        condition = "auto_promo_group_assigned = 1"
    elif dialect_name == 'sqlite':
        condition = "auto_promo_group_assigned = 1"
    else:
        condition = "auto_promo_group_assigned IS TRUE"

    if _column_exists(sa.inspect(bind)):
        op.execute(
            sa.text(
                f"UPDATE {USERS_TABLE} "
                f"SET {COLUMN_NAME} = promo_group_id "
                f"WHERE {COLUMN_NAME} IS NULL AND {condition}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _foreign_key_exists(inspector):
        try:
            op.drop_constraint(FK_NAME, USERS_TABLE, type_='foreignkey')
        except Exception:
            pass

    inspector = sa.inspect(bind)

    if _column_exists(inspector):
        op.drop_column(USERS_TABLE, COLUMN_NAME)
