"""Tests for subscription CRUD helpers."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
import asyncio

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database.crud.subscription import extend_subscription
from app.database.models import Subscription, SubscriptionStatus


def test_extend_subscription_reactivates_disabled_status(monkeypatch):
    """Extend should reactivate disabled subscriptions when days are added."""

    subscription = Subscription(
        user_id=1,
        status=SubscriptionStatus.DISABLED.value,
        is_trial=False,
        start_date=datetime.utcnow() - timedelta(days=30),
        end_date=datetime.utcnow() - timedelta(days=1),
    )
    subscription.id = 123

    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    async def _clear_notifications_stub(db_session, subscription_id):  # noqa: ANN001
        assert db_session is db
        assert subscription_id == subscription.id

    monkeypatch.setattr(
        "app.database.crud.subscription.clear_notifications",
        _clear_notifications_stub,
    )

    asyncio.run(extend_subscription(db, subscription, days=10))

    assert subscription.status == SubscriptionStatus.ACTIVE.value
