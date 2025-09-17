from typing import AsyncIterable

from dishka import Scope, provide, Provider
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncEngine, AsyncSession

from app.api.infrastructure.session_maker import create_sessionmaker
from app.database.database import engine as db_engine


class DatabaseProvider(Provider):
    """DI Scope отвечающий за контейнеры под базу данных"""
    scope = Scope.APP

    @provide
    async def get_async_engine(
        self
    ) -> AsyncEngine:
        return db_engine

    @provide
    def get_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_sessionmaker(engine)

    @provide(scope=Scope.REQUEST)
    async def get_session(
        self, sessionmaker: async_sessionmaker[AsyncSession]
    ) -> AsyncIterable[AsyncSession]:
        session = sessionmaker()
        try:
            yield session
        finally:
            await session.close()