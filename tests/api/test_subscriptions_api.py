from datetime import datetime, timedelta

import pytest

from sqlalchemy import select

from app.database.models import Subscription, User


@pytest.mark.asyncio
async def test_extend_subscription_creates_new_subscription(api_client, session_factory):
    async with session_factory() as session:
        user = User(
            telegram_id=111222,
            username="sub_user",
            status="active",
            language="ru",
            balance_kopeks=0,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    response = await api_client.post(
        f"/api/v1/subscriptions/{user_id}/extend",
        json={"days": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == user_id
    end_date = datetime.fromisoformat(payload["end_date"])
    delta = end_date - datetime.utcnow()
    assert delta >= timedelta(days=4)

    async with session_factory() as session:
        subscription_result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        assert subscription_result.scalars().first() is not None


@pytest.mark.asyncio
async def test_extend_subscription_updates_existing(api_client, session_factory):
    async with session_factory() as session:
        user = User(
            telegram_id=222333,
            username="existing_sub",
            status="active",
            language="ru",
            balance_kopeks=0,
        )
        session.add(user)
        await session.flush()

        subscription = Subscription(
            user_id=user.id,
            status="active",
            is_trial=False,
            start_date=datetime.utcnow() - timedelta(days=10),
            end_date=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=0,
            traffic_used_gb=0.0,
            device_limit=1,
            connected_squads=[],
        )
        session.add(subscription)
        await session.commit()
        user_id = user.id

    response = await api_client.post(
        f"/api/v1/subscriptions/{user_id}/extend",
        json={"days": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == user_id
    updated_end_date = datetime.fromisoformat(payload["end_date"])
    delta = updated_end_date - datetime.utcnow()
    assert delta >= timedelta(days=4)
