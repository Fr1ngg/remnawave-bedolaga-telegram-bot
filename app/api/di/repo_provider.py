from typing import AsyncIterable

from dishka import Scope, Provider, provide
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.infrastructure.db import HolderRepo


class RepoProvider(Provider):
    """DI Scope отвечающий за контейнеры репозитории и их holders"""
    scope = Scope.REQUEST

    @provide
    async def get_holder_repo(self, session: AsyncSession) -> AsyncIterable[HolderRepo]:
        """Хранилище репозиториев, для удобного доступна к ним"""
        yield HolderRepo(session)
