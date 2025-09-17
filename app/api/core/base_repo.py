from datetime import datetime

from typing import TypeVar, Generic, Optional

from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Base

Model = TypeVar("Model", bound=Base, covariant=True, contravariant=False)


class BaseRepo(Generic[Model]):
    """
    A class representing a base repository for handling database operations.

    Attributes:
        session (AsyncSession): The database session used by the repository.
        model (Model): The database table model.
    """

    def __init__(self, session: AsyncSession, model: type[Model]) -> None:
        self.session: AsyncSession = session
        self.model = model

    async def _get_by_id(self, user_id: int):
        r = await self.session.get(
            self.model,
            user_id,
        )
        if r is None:
            raise NoResultFound
        return r

    def _build_time_conditions(
        self,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
    ):
        conditions = []
        if created_after:
            conditions.append(self.model.created_at >= created_after)
        if created_before:
            conditions.append(self.model.created_at <= created_before)
        return conditions

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()