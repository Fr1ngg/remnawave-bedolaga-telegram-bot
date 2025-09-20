"""add promo groups table and link users"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


PROMO_GROUPS_TABLE = "promo_groups"
USERS_TABLE = "users"
PROMO_GROUP_COLUMN = "promo_group_id"
PROMO_GROUP_INDEX = "ix_users_promo_group_id"
PROMO_GROUP_FK = "fk_users_promo_group_id_promo_groups"
DEFAULT_PROMO_GROUP_NAME = "Базовый юзер"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _foreign_key_exists(inspector: sa.Inspector, table_name: str, fk_name: str) -> bool:
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))

revision: str = "1f5f3a3f5a4d"
down_revision: Union[str, None] = "cbd1be472f3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, PROMO_GROUPS_TABLE):
        op.create_table(
            PROMO_GROUPS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column(
                "server_discount_percent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "traffic_discount_percent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "device_discount_percent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("name", name="uq_promo_groups_name"),
        )
        inspector = sa.inspect(bind)

    if not _column_exists(inspector, USERS_TABLE, PROMO_GROUP_COLUMN):
        op.add_column(
            USERS_TABLE,
            sa.Column(PROMO_GROUP_COLUMN, sa.Integer(), nullable=True),
        )
        inspector = sa.inspect(bind)

    if _column_exists(inspector, USERS_TABLE, PROMO_GROUP_COLUMN):
        if not _index_exists(inspector, USERS_TABLE, PROMO_GROUP_INDEX):
            op.create_index(PROMO_GROUP_INDEX, USERS_TABLE, [PROMO_GROUP_COLUMN])

        inspector = sa.inspect(bind)
        if not _foreign_key_exists(inspector, USERS_TABLE, PROMO_GROUP_FK):
            op.create_foreign_key(
                PROMO_GROUP_FK,
                USERS_TABLE,
                PROMO_GROUPS_TABLE,
                [PROMO_GROUP_COLUMN],
                ["id"],
                ondelete="RESTRICT",
            )

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, PROMO_GROUPS_TABLE) or not _column_exists(
        inspector, USERS_TABLE, PROMO_GROUP_COLUMN
    ):
        return

    promo_groups_table = sa.table(
        PROMO_GROUPS_TABLE,
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("server_discount_percent", sa.Integer()),
        sa.column("traffic_discount_percent", sa.Integer()),
        sa.column("device_discount_percent", sa.Integer()),
        sa.column("is_default", sa.Boolean()),
    )

    connection = bind
    existing_named_group = (
        connection.execute(
            sa.select(
                promo_groups_table.c.id,
                promo_groups_table.c.is_default,
            )
            .where(promo_groups_table.c.name == DEFAULT_PROMO_GROUP_NAME)
            .limit(1)
        )
        .mappings()
        .first()
    )

    if existing_named_group:
        default_group_id = existing_named_group["id"]
        if not existing_named_group["is_default"]:
            connection.execute(
                sa.update(promo_groups_table)
                .where(promo_groups_table.c.id == default_group_id)
                .values(is_default=True)
            )
    else:
        default_group_id = connection.execute(
            sa.select(promo_groups_table.c.id)
            .where(promo_groups_table.c.is_default.is_(True))
            .limit(1)
        ).scalar_one_or_none()

        if default_group_id is None:
            default_group_id = connection.execute(
                sa.insert(promo_groups_table)
                .values(
                    name=DEFAULT_PROMO_GROUP_NAME,
                    server_discount_percent=0,
                    traffic_discount_percent=0,
                    device_discount_percent=0,
                    is_default=True,
                )
                .returning(promo_groups_table.c.id)
            ).scalar_one()

    users_table = sa.table(
        USERS_TABLE,
        sa.column("promo_group_id", sa.Integer()),
    )
    connection.execute(
        sa.update(users_table)
        .where(users_table.c.promo_group_id.is_(None))
        .values(promo_group_id=default_group_id)
    )

    inspector = sa.inspect(bind)
    column_info = next(
        (col for col in inspector.get_columns(USERS_TABLE) if col["name"] == PROMO_GROUP_COLUMN),
        None,
    )
    if column_info and column_info.get("nullable", True):
        op.alter_column(
            USERS_TABLE,
            PROMO_GROUP_COLUMN,
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, USERS_TABLE, PROMO_GROUP_COLUMN):
        column_info = next(
            (
                col
                for col in inspector.get_columns(USERS_TABLE)
                if col["name"] == PROMO_GROUP_COLUMN
            ),
            None,
        )
        if column_info and not column_info.get("nullable", False):
            op.alter_column(
                USERS_TABLE,
                PROMO_GROUP_COLUMN,
                existing_type=sa.Integer(),
                nullable=True,
            )

        inspector = sa.inspect(bind)
        if _foreign_key_exists(inspector, USERS_TABLE, PROMO_GROUP_FK):
            op.drop_constraint(PROMO_GROUP_FK, USERS_TABLE, type_="foreignkey")

        inspector = sa.inspect(bind)
        if _index_exists(inspector, USERS_TABLE, PROMO_GROUP_INDEX):
            op.drop_index(PROMO_GROUP_INDEX, table_name=USERS_TABLE)

        op.drop_column(USERS_TABLE, PROMO_GROUP_COLUMN)

    inspector = sa.inspect(bind)
    if _table_exists(inspector, PROMO_GROUPS_TABLE):
        op.drop_table(PROMO_GROUPS_TABLE)
