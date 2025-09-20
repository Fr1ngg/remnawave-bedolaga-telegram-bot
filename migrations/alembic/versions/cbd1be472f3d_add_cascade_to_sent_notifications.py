"""add cascade delete to sent notifications"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


TABLE_NAME = "sent_notifications"
OLD_USER_FK = "sent_notifications_user_id_fkey"
OLD_SUBSCRIPTION_FK = "sent_notifications_subscription_id_fkey"
NEW_USER_FK = "fk_sent_notifications_user_id_users"
NEW_SUBSCRIPTION_FK = "fk_sent_notifications_subscription_id_subscriptions"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _foreign_key_exists(inspector: sa.Inspector, table_name: str, fk_name: str) -> bool:
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))

revision: str = 'cbd1be472f3d'
down_revision: Union[str, None] = '8fd1e338eb45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, TABLE_NAME):
        return

    if _foreign_key_exists(inspector, TABLE_NAME, OLD_USER_FK):
        op.drop_constraint(OLD_USER_FK, TABLE_NAME, type_="foreignkey")

    inspector = sa.inspect(bind)
    if _foreign_key_exists(inspector, TABLE_NAME, OLD_SUBSCRIPTION_FK):
        op.drop_constraint(OLD_SUBSCRIPTION_FK, TABLE_NAME, type_="foreignkey")

    inspector = sa.inspect(bind)
    if not _foreign_key_exists(inspector, TABLE_NAME, NEW_USER_FK):
        op.create_foreign_key(
            NEW_USER_FK,
            TABLE_NAME,
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    inspector = sa.inspect(bind)
    if not _foreign_key_exists(inspector, TABLE_NAME, NEW_SUBSCRIPTION_FK):
        op.create_foreign_key(
            NEW_SUBSCRIPTION_FK,
            TABLE_NAME,
            "subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, TABLE_NAME):
        return

    if _foreign_key_exists(inspector, TABLE_NAME, NEW_USER_FK):
        op.drop_constraint(NEW_USER_FK, TABLE_NAME, type_="foreignkey")

    inspector = sa.inspect(bind)
    if _foreign_key_exists(inspector, TABLE_NAME, NEW_SUBSCRIPTION_FK):
        op.drop_constraint(NEW_SUBSCRIPTION_FK, TABLE_NAME, type_="foreignkey")

    inspector = sa.inspect(bind)
    if not _foreign_key_exists(inspector, TABLE_NAME, OLD_USER_FK):
        op.create_foreign_key(OLD_USER_FK, TABLE_NAME, "users", ["user_id"], ["id"])

    inspector = sa.inspect(bind)
    if not _foreign_key_exists(inspector, TABLE_NAME, OLD_SUBSCRIPTION_FK):
        op.create_foreign_key(
            OLD_SUBSCRIPTION_FK,
            TABLE_NAME,
            "subscriptions",
            ["subscription_id"],
            ["id"],
        )
