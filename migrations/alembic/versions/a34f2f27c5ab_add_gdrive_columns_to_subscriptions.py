from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector


revision: str = 'a34f2f27c5ab'
down_revision: Union[str, None] = '8fd1e338eb45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = 'subscriptions'
GDRIVE_FILE_COLUMN = 'gdrive_file_id'
GDRIVE_LINK_COLUMN = 'gdrive_link'


def _column_exists(inspector: Inspector, column_name: str) -> bool:
    return column_name in {column['name'] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, GDRIVE_FILE_COLUMN):
        op.add_column(TABLE_NAME, sa.Column(GDRIVE_FILE_COLUMN, sa.String(), nullable=True))

    if not _column_exists(inspector, GDRIVE_LINK_COLUMN):
        op.add_column(TABLE_NAME, sa.Column(GDRIVE_LINK_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, GDRIVE_LINK_COLUMN):
        op.drop_column(TABLE_NAME, GDRIVE_LINK_COLUMN)

    if _column_exists(inspector, GDRIVE_FILE_COLUMN):
        op.drop_column(TABLE_NAME, GDRIVE_FILE_COLUMN)
