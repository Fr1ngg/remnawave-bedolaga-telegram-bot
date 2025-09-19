"""add advertising campaigns tables"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d1f1f8b2e9a"
down_revision: Union[str, None] = "cbd1be472f3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "advertising_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("start_parameter", sa.String(length=64), nullable=False),
        sa.Column("bonus_type", sa.String(length=20), nullable=False),
        sa.Column("balance_bonus_kopeks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subscription_duration_days", sa.Integer(), nullable=True),
        sa.Column("subscription_traffic_gb", sa.Integer(), nullable=True),
        sa.Column("subscription_device_limit", sa.Integer(), nullable=True),
        sa.Column("subscription_squads", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_advertising_campaigns_start_parameter",
        "advertising_campaigns",
        ["start_parameter"],
        unique=True,
    )
    op.create_index(
        "ix_advertising_campaigns_id",
        "advertising_campaigns",
        ["id"],
    )

    op.create_table(
        "advertising_campaign_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bonus_type", sa.String(length=20), nullable=False),
        sa.Column("balance_bonus_kopeks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subscription_duration_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["advertising_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("campaign_id", "user_id", name="uq_campaign_user"),
    )
    op.create_index(
        "ix_advertising_campaign_registrations_id",
        "advertising_campaign_registrations",
        ["id"],
    )


def downgrade() -> None:
    op.drop_index("ix_advertising_campaign_registrations_id", table_name="advertising_campaign_registrations")
    op.drop_table("advertising_campaign_registrations")
    op.drop_index("ix_advertising_campaigns_id", table_name="advertising_campaigns")
    op.drop_index("ix_advertising_campaigns_start_parameter", table_name="advertising_campaigns")
    op.drop_table("advertising_campaigns")
