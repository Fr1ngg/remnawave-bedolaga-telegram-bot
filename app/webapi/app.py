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
    media,
    miniapp,
    polls,
    promocodes,
    promo_groups,
    promo_offers,
    pages,
    remnawave,
    servers,
    stats,
    subscriptions,
    tickets,
    tokens,
    transactions,
    users,
    logs,
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
        "name": "servers",
        "description": (
            "Управление серверами RemnaWave, их доступностью, промогруппами и "
            "ручная синхронизация данных.",
        ),
    },
    {
        "name": "promo-offers",
        "description": "Управление промо-предложениями, шаблонами и журналом событий.",
    },
    {
        "name": "logs",
        "description": (
            "Журналы мониторинга бота, действий модераторов поддержки и системный лог-файл."
        ),
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
        "name": "media",
        "description": "Загрузка файлов в Telegram и получение ссылок на медиа.",
    },
    {
        "name": "miniapp",
        "description": "Endpoint для Telegram Mini App с информацией о подписке пользователя.",
    },
    {
        "name": "polls",
        "description": "Создание опросов, удаление, статистика и ответы пользователей.",
    },
    {
        "name": "pages",
        "description": "Управление контентом публичных страниц: оферта, политика, FAQ и правила.",
    },
]


def create_web_api_app() -> FastAPI:
    docs_config = settings.get_web_api_docs_config()

    # Убираем openapi_tags для предотвращения ошибок при генерации openapi.json
    app = FastAPI(
        title=settings.WEB_API_TITLE,
        version=settings.WEB_API_VERSION,
        docs_url=docs_config.get("docs_url"),
        redoc_url=docs_config.get("redoc_url"),
        openapi_url=docs_config.get("openapi_url"),
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
    app.include_router(stats.router, prefix="/stats", tags=["stats"])
    app.include_router(config.router, prefix="/settings", tags=["settings"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
    app.include_router(tickets.router, prefix="/tickets", tags=["support"])
    app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
    app.include_router(promo_groups.router, prefix="/promo-groups", tags=["promo-groups"])
    app.include_router(promo_offers.router, prefix="/promo-offers", tags=["promo-offers"])
    app.include_router(servers.router, prefix="/servers", tags=["servers"])
    app.include_router(
        main_menu_buttons.router,
        prefix="/main-menu/buttons",
        tags=["main-menu"],
    )
    app.include_router(pages.router, prefix="/pages", tags=["pages"])
    app.include_router(promocodes.router, prefix="/promo-codes", tags=["promo-codes"])
    app.include_router(broadcasts.router, prefix="/broadcasts", tags=["broadcasts"])
    app.include_router(backups.router, prefix="/backups", tags=["backups"])
    app.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
    app.include_router(tokens.router, prefix="/tokens", tags=["auth"])
    app.include_router(remnawave.router, prefix="/remnawave", tags=["remnawave"])
    app.include_router(media.router, tags=["media"])
    app.include_router(miniapp.router, prefix="/miniapp", tags=["miniapp"])
    app.include_router(polls.router, prefix="/polls", tags=["polls"])
    app.include_router(logs.router, prefix="/logs", tags=["logs"])

    return app
