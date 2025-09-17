from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncEngine, AsyncSession

def create_sessionmaker(
    engine: AsyncEngine, **kwargs
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, **kwargs)
