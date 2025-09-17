from dishka import make_async_container, AsyncContainer

from app.api.di.db_provider import DatabaseProvider
from app.api.di.repo_provider import RepoProvider
from app.api.di.service_provider import ServiceProvider


def setup_di_fastapi() -> AsyncContainer:
    container = make_async_container(
        *[DatabaseProvider(), RepoProvider(), ServiceProvider()]
    )
    return container