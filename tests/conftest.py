import os
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


# Ensure required environment variables are present before importing application modules
os.environ.setdefault("BOT_TOKEN", "TEST_BOT_TOKEN")
os.environ.setdefault("REMNAWAVE_API_URL", "http://remnawave.test")
os.environ.setdefault("REMNAWAVE_API_KEY", "test-key")
os.environ.setdefault("TRIAL_SQUAD_UUID", "test-squad")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./tests/test_api.db")
os.environ.setdefault("TOKEN_BOT_API", "test-api-token")

from app.api.main import create_app  # noqa: E402
from app.api.domain.services import subscription_service as subscription_service_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import database as database_module  # noqa: E402
from app.api.di import db_provider as db_provider_module  # noqa: E402
from app.database.models import Base  # noqa: E402


TEST_DB_PATH = Path("tests/test_api.db")


class _DummyRemnawaveUser:
    subscription_url = "https://remnawave.test/subscription"


class _StubCoreSubscriptionService:
    async def update_remnawave_user(self, session, subscription):  # pragma: no cover - trivial stub
        return _DummyRemnawaveUser()


@pytest.fixture(scope="session")
async def test_engine() -> AsyncEngine:
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    engine = create_async_engine(
        settings.DATABASE_URL,
        future=True,
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original_engine = database_module.engine
    original_provider_engine = db_provider_module.db_engine
    database_module.engine = engine
    db_provider_module.db_engine = engine

    yield engine

    database_module.engine = original_engine
    db_provider_module.db_engine = original_provider_engine
    await engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture(scope="session")
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_database(test_engine: AsyncEngine):
    yield
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
async def api_client(monkeypatch, test_engine: AsyncEngine):
    monkeypatch.setattr(
        subscription_service_module,
        "CoreSubscriptionService",
        lambda: _StubCoreSubscriptionService(),
    )

    app = create_app()

    async with AsyncClient(app=app, base_url="http://test") as client:
        client.headers["x-api-key"] = settings.TOKEN_BOT_API
        yield client
