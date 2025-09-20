"""add advertising campaigns tables"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


CAMPAIGNS_TABLE = "advertising_campaigns"
CAMPAIGNS_START_INDEX = "ix_advertising_campaigns_start_parameter"
CAMPAIGNS_ID_INDEX = "ix_advertising_campaigns_id"
REGISTRATIONS_TABLE = "advertising_campaign_registrations"
REGISTRATIONS_ID_INDEX = "ix_advertising_campaign_registrations_id"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


revision: str = "5d1f1f8b2e9a"
down_revision: Union[str, None] = "cbd1be472f3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, CAMPAIGNS_TABLE):
        op.create_table(
            CAMPAIGNS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("start_parameter", sa.String(length=64), nullable=False),
            sa.Column("bonus_type", sa.String(length=20), nullable=False),
            sa.Column(
                "balance_bonus_kopeks",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("subscription_duration_days", sa.Integer(), nullable=True),
            sa.Column("subscription_traffic_gb", sa.Integer(), nullable=True),
            sa.Column("subscription_device_limit", sa.Integer(), nullable=True),
            sa.Column("subscription_squads", sa.JSON(), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        )
        inspector = sa.inspect(bind)

    if not _index_exists(inspector, CAMPAIGNS_TABLE, CAMPAIGNS_START_INDEX):
        op.create_index(
            CAMPAIGNS_START_INDEX,
            CAMPAIGNS_TABLE,
            ["start_parameter"],
            unique=True,
        )

    inspector = sa.inspect(bind)
    if not _index_exists(inspector, CAMPAIGNS_TABLE, CAMPAIGNS_ID_INDEX):
        op.create_index(CAMPAIGNS_ID_INDEX, CAMPAIGNS_TABLE, ["id"])

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, REGISTRATIONS_TABLE):
        op.create_table(
            REGISTRATIONS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("bonus_type", sa.String(length=20), nullable=False),
            sa.Column(
                "balance_bonus_kopeks",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("subscription_duration_days", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["campaign_id"],
                [f"{CAMPAIGNS_TABLE}.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("campaign_id", "user_id", name="uq_campaign_user"),
        )
        inspector = sa.inspect(bind)

    if not _index_exists(inspector, REGISTRATIONS_TABLE, REGISTRATIONS_ID_INDEX):
        op.create_index(
            REGISTRATIONS_ID_INDEX,
            REGISTRATIONS_TABLE,
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, REGISTRATIONS_TABLE, REGISTRATIONS_ID_INDEX):
        op.drop_index(REGISTRATIONS_ID_INDEX, table_name=REGISTRATIONS_TABLE)

    inspector = sa.inspect(bind)
    if _table_exists(inspector, REGISTRATIONS_TABLE):
        op.drop_table(REGISTRATIONS_TABLE)

    inspector = sa.inspect(bind)
    if _index_exists(inspector, CAMPAIGNS_TABLE, CAMPAIGNS_ID_INDEX):
        op.drop_index(CAMPAIGNS_ID_INDEX, table_name=CAMPAIGNS_TABLE)

    inspector = sa.inspect(bind)
    if _index_exists(inspector, CAMPAIGNS_TABLE, CAMPAIGNS_START_INDEX):
        op.drop_index(CAMPAIGNS_START_INDEX, table_name=CAMPAIGNS_TABLE)

    inspector = sa.inspect(bind)
    if _table_exists(inspector, CAMPAIGNS_TABLE):
        op.drop_table(CAMPAIGNS_TABLE)
