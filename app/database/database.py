import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

QueuePool = AsyncAdaptedQueuePool


engine = create_async_engine(
    settings.get_database_url(),
    poolclass=QueuePool,
    echo=settings.DEBUG,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    logger.info("Создание таблиц базы данных...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("✅ База данных успешно инициализирована")


async def close_db():
    await engine.dispose()
    logger.info("✅ Подключение к базе данных закрыто")
