from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.domain.schemas.exception.base import ErrorResponse


def custom_openapi(app: FastAPI):
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Добавляем глобальные ошибки
    error_schema = ErrorResponse.schema()
    for path in openapi_schema["paths"].values():
        for method in path.values():
            method.setdefault("responses", {})
            method["responses"].setdefault("400", {
                "description": "Bad Request",
                "content": {"application/json": {"schema": error_schema}},
            })
            method["responses"].setdefault("404", {
                "description": "Not Found",
                "content": {"application/json": {"schema": error_schema}},
            })
            method["responses"].setdefault("500", {
                "description": "Internal Server Error",
                "content": {"application/json": {"schema": error_schema}},
            })

    app.openapi_schema = openapi_schema
    return app.openapi_schema