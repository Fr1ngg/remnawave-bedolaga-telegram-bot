from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

from .middleware import RequestLoggingMiddleware
from .routes import (
    broadcasts,
    backups,
    campaigns,
    config,
    health,
    main_menu_buttons,
    promocodes,
    miniapp,
    promo_groups,
    promo_offers,
    pages,
    remnawave,
    stats,
    system_metrics,
    subscriptions,
    tickets,
    tokens,
    transactions,
    users,
)


OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Мониторинг состояния административного API и связанных сервисов.",
    },
    {
        "name": "stats",
        "description": "Сводные показатели по пользователям, подпискам и платежам.",
    },
    {
        "name": "settings",
        "description": "Получение и изменение конфигурации бота из административной панели.",
    },
    {
        "name": "main-menu",
        "description": "Управление кнопками главного меню Telegram-бота.",
    },
    {
        "name": "users",
        "description": "Управление пользователями, балансом и статусами подписок.",
    },
    {
        "name": "subscriptions",
        "description": "Создание, продление и настройка подписок бота.",
    },
    {
        "name": "support",
        "description": "Работа с тикетами поддержки, приоритетами и ограничениями на ответы.",
    },
    {
        "name": "transactions",
        "description": "История финансовых операций и пополнений баланса.",
    },
    {
        "name": "promo-groups",
        "description": "Создание и управление промо-группами и их участниками.",
    },
    {
        "name": "promo-offers",
        "description": "Управление промо-предложениями, шаблонами и журналом событий.",
    },
    {
        "name": "auth",
        "description": "Управление токенами доступа к административному API.",
    },
    {
        "name": "remnawave",
        "description": (
            "Интеграция с RemnaWave: статус панели, управление нодами, сквадами и синхронизацией "
            "данных между ботом и панелью."
        ),
    },
    {
        "name": "miniapp",
        "description": "Endpoint для Telegram Mini App с информацией о подписке пользователя.",
    },
    {
        "name": "pages",
        "description": "Управление контентом публичных страниц: оферта, политика, FAQ и правила.",
    },
]


def create_web_api_app() -> FastAPI:
    # Docs served dynamically through routes/apidocs depending on setting
    app = FastAPI(
        title=settings.WEB_API_TITLE,
        version=settings.WEB_API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        openapi_tags=OPENAPI_TAGS,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    allowed_origins = settings.get_web_api_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allowed_origins == ["*"] else allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.WEB_API_REQUEST_LOGGING:
        app.add_middleware(RequestLoggingMiddleware)

    app.include_router(health.router)
    from .routes import apidocs
    app.include_router(apidocs.router)
    from .routes import notifications
    app.include_router(notifications.router, prefix="/notifications", tags=["notifications"]) 
    from .routes import auth as auth_routes
    app.include_router(auth_routes.router)
    app.include_router(stats.router, prefix="/stats", tags=["stats"])
    app.include_router(system_metrics.router)
    app.include_router(config.router, prefix="/settings", tags=["settings"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
    app.include_router(tickets.router, prefix="/tickets", tags=["support"])
    app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
    app.include_router(promo_groups.router, prefix="/promo-groups", tags=["promo-groups"])
    app.include_router(promo_offers.router, prefix="/promo-offers", tags=["promo-offers"])
    app.include_router(
        main_menu_buttons.router,
        prefix="/main-menu/buttons",
        tags=["main-menu"],
    )
    app.include_router(pages.router, prefix="/pages", tags=["pages"])
    app.include_router(promocodes.router, prefix="/promo-codes", tags=["promo-codes"])
    app.include_router(broadcasts.router, prefix="/broadcasts", tags=["broadcasts"])
    from .routes import logs as logs_routes
    app.include_router(logs_routes.router)
    app.include_router(backups.router, prefix="/backups", tags=["backups"])
    app.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
    app.include_router(tokens.router, prefix="/tokens", tags=["auth"])
    app.include_router(remnawave.router, prefix="/remnawave", tags=["remnawave"])
    app.include_router(miniapp.router, prefix="/miniapp", tags=["miniapp"])

    # Debug 204 routes: log any route configured with 204 that may return a body
    try:  # pragma: no cover - diagnostic helper
        from fastapi.routing import APIRoute
        import logging as _logging
        _log = _logging.getLogger("web_api")
        for route in app.router.routes:
            if isinstance(route, APIRoute):
                status_code = getattr(route, "status_code", None)
                if status_code == 204:
                    _log.debug(
                        "Route 204 configured: methods=%s path=%s response_model=%s response_class=%s",
                        sorted(list(route.methods or [])),
                        route.path,
                        getattr(route, "response_model", None),
                        getattr(route, "response_class", None),
                    )
    except Exception:
        pass

    return app
