import pytest

from app.database.models import User


@pytest.mark.asyncio
async def test_search_users_returns_matching_prefix(api_client, session_factory):
    async with session_factory() as session:
        user = User(
            telegram_id=123456,
            username="testprefix_user",
            status="active",
            language="ru",
            balance_kopeks=1000,
        )
        session.add(user)
        await session.commit()

    response = await api_client.get("/api/v1/users/search", params={"username_prefix": "test"})

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["username"] == "testprefix_user"
    assert data[0]["balance_kopeks"] == 1000


@pytest.mark.asyncio
async def test_update_user_balance(api_client, session_factory):
    async with session_factory() as session:
        user = User(
            telegram_id=999999,
            username="balance_user",
            status="active",
            language="ru",
            balance_kopeks=500,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    response = await api_client.patch(
        f"/api/v1/users/{user_id}/balance",
        json={"new_balance": 1550},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == user_id
    assert payload["balance_kopeks"] == 1550

    async with session_factory() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.balance_kopeks == 1550
