"""aiohttp server exposing the bot web admin API and UI."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.services.backup_service import BackupService
from app.services.maintenance_service import MaintenanceService
from app.services.monitoring_service import MonitoringService
from app.services.reporting_service import ReportPeriod, ReportingService
from app.services.system_settings_service import bot_configuration_service
from app.services.version_service import VersionService
from app.webadmin.dashboard import (
    collect_dashboard_summary,
    collect_revenue_series,
    fetch_recent_users,
    fetch_server_overview,
    get_user_details,
    list_users,
)

logger = logging.getLogger(__name__)


class WebAdminServer:
    """Expose a modern web admin backed by the bot data."""

    _PUBLIC_PATHS: set[str] = {"/", "/api/auth/login", "/api/health"}

    def __init__(
        self,
        bot: Bot,
        *,
        maintenance_service: MaintenanceService,
        monitoring_service: MonitoringService,
        reporting_service: ReportingService,
        version_service: VersionService,
        backup_service: BackupService,
    ) -> None:
        self.bot = bot
        self.maintenance_service = maintenance_service
        self.monitoring_service = monitoring_service
        self.reporting_service = reporting_service
        self.version_service = version_service
        self.backup_service = backup_service

        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

        project_root = Path(__file__).resolve().parents[2]
        self._index_path = project_root / "webadmin" / "index.html"
        try:
            self._index_template = self._index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("Не найден шаблон веб-админки: %s", self._index_path)
            self._index_template = "<h1>Web admin template not found</h1>"

        self._allowed_origins = settings.get_webadmin_allowed_origins()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def create_app(self) -> web.Application:
        if self.app:
            return self.app

        middlewares = self._create_middlewares()
        self.app = web.Application(middlewares=middlewares)
        self._register_routes(self.app)
        return self.app

    async def start(self) -> None:
        if not settings.is_webadmin_enabled():
            logger.info("Веб-админка отключена настройками")
            return

        await self.create_app()

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        host = settings.get_webadmin_host()
        port = settings.get_webadmin_port()

        self.site = web.TCPSite(self.runner, host=host, port=port)
        await self.site.start()

        logger.info("🌐 Веб-админка запущена на http://%s:%s", host, port)

    async def stop(self) -> None:
        if self.site:
            await self.site.stop()
            self.site = None

        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    # ------------------------------------------------------------------
    # Middlewares & helpers
    # ------------------------------------------------------------------
    def _create_middlewares(self) -> List[web.Middleware]:
        @web.middleware
        async def cors_middleware(
            request: web.Request, handler: web.Handler
        ) -> web.StreamResponse:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                self._apply_cors(request, exc)
                raise

            self._apply_cors(request, response)
            return response

        @web.middleware
        async def auth_middleware(
            request: web.Request, handler: web.Handler
        ) -> web.StreamResponse:
            if request.method == "OPTIONS":
                return await handler(request)

            path = request.path
            if path in self._PUBLIC_PATHS or not path.startswith("/api/"):
                return await handler(request)

            expected = (settings.WEBADMIN_API_KEY or "").strip()
            if not expected:
                return self._error("Веб-админка не настроена", status=503)

            token = self._extract_token(request)
            if not token:
                return self._error("Требуется авторизация", status=401)

            if not hmac.compare_digest(expected, token):
                return self._error("Неверный токен доступа", status=401)

            request["webadmin_token"] = token
            return await handler(request)

        return [cors_middleware, auth_middleware]

    def _apply_cors(self, request: web.Request, response: web.StreamResponse) -> None:
        origin = request.headers.get("Origin")
        allow_origin = "*"
        if self._allowed_origins:
            if origin and origin in self._allowed_origins:
                allow_origin = origin
            elif "*" not in self._allowed_origins:
                allow_origin = self._allowed_origins[0]

        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Credentials"] = "false"

    @staticmethod
    def _extract_token(request: web.Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()

        token = request.cookies.get("webadmin_token")
        if token:
            return token.strip()

        token = request.query.get("token")
        if token:
            return token.strip()

        return None

    def _register_routes(self, app: web.Application) -> None:
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/api/health", self.handle_health)
        app.router.add_post("/api/auth/login", self.handle_login)

        app.router.add_get("/api/dashboard/summary", self.handle_dashboard_summary)
        app.router.add_get("/api/dashboard/revenue", self.handle_dashboard_revenue)

        app.router.add_get("/api/users/recent", self.handle_recent_users)
        app.router.add_get("/api/users", self.handle_users)
        app.router.add_get("/api/users/{user_id}", self.handle_user_details)

        app.router.add_get("/api/servers", self.handle_servers)

        app.router.add_get("/api/settings/categories", self.handle_settings_categories)
        app.router.add_get("/api/settings/category/{category_key}", self.handle_settings_category)
        app.router.add_put("/api/settings/{key}", self.handle_setting_update)
        app.router.add_delete("/api/settings/{key}", self.handle_setting_reset)

        app.router.add_post("/api/bot/control", self.handle_bot_control)

        app.router.add_options("/{tail:.*}", self.handle_options)

    def _render_index(self) -> str:
        title = settings.get_webadmin_title()
        return self._index_template.replace("{{WEBADMIN_TITLE}}", title)

    @staticmethod
    def _success(data: Any = None, **extra: Any) -> web.Response:
        payload: Dict[str, Any] = {"status": "ok"}
        if data is not None:
            payload["data"] = data
        if extra:
            payload.update(extra)
        return web.json_response(payload)

    @staticmethod
    def _error(message: str, *, status: int = 400, **extra: Any) -> web.Response:
        payload: Dict[str, Any] = {"status": "error", "message": message}
        if extra:
            payload.update(extra)
        return web.json_response(payload, status=status)

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, dict)):
            return value
        return str(value)

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------
    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=self._render_index(), content_type="text/html")

    async def handle_health(self, request: web.Request) -> web.Response:
        maintenance = self.maintenance_service.status
        data = {
            "webadmin": {
                "enabled": settings.is_webadmin_enabled(),
                "title": settings.get_webadmin_title(),
            },
            "bot": {
                "running": True,
                "maintenance": maintenance.is_active,
                "maintenance_reason": maintenance.reason,
                "version": self.version_service.current_version,
            },
            "services": {
                "monitoring_running": bool(getattr(self.monitoring_service, "is_running", False)),
                "reporting_running": self.reporting_service.is_running(),
                "auto_backup_enabled": getattr(
                    self.backup_service, "_settings", None
                ).auto_backup_enabled
                if getattr(self.backup_service, "_settings", None)
                else False,
            },
        }
        return self._success(data)

    async def handle_login(self, request: web.Request) -> web.Response:
        expected = (settings.WEBADMIN_API_KEY or "").strip()
        if not expected:
            return self._error("Веб-админка не настроена", status=503)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        token = str(payload.get("token", "")).strip()
        if not token:
            return self._error("Укажите токен доступа", status=400)

        if not hmac.compare_digest(expected, token):
            return self._error("Неверный токен доступа", status=401)

        return self._success({"access_token": token, "token_type": "bearer"})

    async def handle_dashboard_summary(self, request: web.Request) -> web.Response:
        async with AsyncSessionLocal() as session:
            data = await collect_dashboard_summary(session)
        return self._success(data)

    async def handle_dashboard_revenue(self, request: web.Request) -> web.Response:
        try:
            days = int(request.query.get("days", "14"))
        except ValueError:
            days = 14

        async with AsyncSessionLocal() as session:
            data = await collect_revenue_series(session, days=days)
        return self._success(data)

    async def handle_recent_users(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "8"))
        except ValueError:
            limit = 8

        async with AsyncSessionLocal() as session:
            data = await fetch_recent_users(session, limit=limit)
        return self._success(data)

    async def handle_users(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            limit = 20
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            offset = 0
        search = request.query.get("search")

        async with AsyncSessionLocal() as session:
            items, total = await list_users(
                session, limit=limit, offset=offset, search=search
            )

        return self._success({"items": items, "total": total, "limit": limit, "offset": offset})

    async def handle_user_details(self, request: web.Request) -> web.Response:
        try:
            user_id = int(request.match_info["user_id"])
        except (KeyError, ValueError):
            return self._error("Некорректный идентификатор пользователя", status=400)

        async with AsyncSessionLocal() as session:
            data = await get_user_details(session, user_id)

        if not data:
            return self._error("Пользователь не найден", status=404)

        return self._success(data)

    async def handle_servers(self, request: web.Request) -> web.Response:
        async with AsyncSessionLocal() as session:
            data = await fetch_server_overview(session)
        return self._success(data)

    async def handle_settings_categories(self, request: web.Request) -> web.Response:
        categories = [
            {"key": key, "label": label, "count": count}
            for key, label, count in bot_configuration_service.get_categories()
        ]
        categories.sort(key=lambda item: item["label"].lower())
        return self._success(categories)

    async def handle_settings_category(self, request: web.Request) -> web.Response:
        category_key = request.match_info.get("category_key")
        definitions = bot_configuration_service.get_settings_for_category(category_key)

        items: List[Dict[str, Any]] = []
        for definition in definitions:
            key = definition.key
            current_value = bot_configuration_service.get_current_value(key)
            original_value = bot_configuration_service.get_original_value(key)
            items.append(
                {
                    "key": key,
                    "name": definition.display_name,
                    "type": definition.type_label,
                    "is_optional": definition.is_optional,
                    "has_override": bot_configuration_service.has_override(key),
                    "value": self._serialize_value(current_value),
                    "value_formatted": bot_configuration_service.format_value(current_value),
                    "original": self._serialize_value(original_value),
                    "original_formatted": bot_configuration_service.format_value(original_value),
                    "choices": [
                        {
                            "value": choice.value,
                            "label": choice.label,
                            "description": choice.description,
                        }
                        for choice in bot_configuration_service.get_choice_options(key)
                    ],
                }
            )

        return self._success(items)

    async def handle_setting_update(self, request: web.Request) -> web.Response:
        key = request.match_info.get("key")
        if not key:
            return self._error("Не указан ключ настройки", status=400)

        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return self._error("Настройка не найдена", status=404)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        if "value" not in payload:
            return self._error("Отсутствует поле value", status=400)

        raw_value = payload.get("value")
        try:
            parsed_value = (
                bot_configuration_service.parse_user_value(key, str(raw_value))
                if raw_value is not None
                else None
            )
        except ValueError as exc:
            return self._error(str(exc), status=400)

        async with AsyncSessionLocal() as session:
            await bot_configuration_service.set_value(session, key, parsed_value)

        updated = bot_configuration_service.get_current_value(key)
        summary = {
            "key": key,
            "value": self._serialize_value(updated),
            "value_formatted": bot_configuration_service.format_value(updated),
            "has_override": bot_configuration_service.has_override(key),
        }
        return self._success(summary)

    async def handle_setting_reset(self, request: web.Request) -> web.Response:
        key = request.match_info.get("key")
        if not key:
            return self._error("Не указан ключ настройки", status=400)

        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return self._error("Настройка не найдена", status=404)

        async with AsyncSessionLocal() as session:
            await bot_configuration_service.reset_value(session, key)

        value = bot_configuration_service.get_current_value(key)
        summary = {
            "key": key,
            "value": self._serialize_value(value),
            "value_formatted": bot_configuration_service.format_value(value),
            "has_override": bot_configuration_service.has_override(key),
        }
        return self._success(summary)

    async def handle_bot_control(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        action = (payload.get("action") or "").strip()
        if not action:
            return self._error("Не указано действие", status=400)

        try:
            if action == "reload_configuration":
                await bot_configuration_service.reload()
                return self._success({"message": "Конфигурация перезагружена"})

            if action == "enable_maintenance":
                reason = payload.get("reason") or "Включено из веб-админки"
                success = await self.maintenance_service.enable_maintenance(reason=reason)
                if success:
                    return self._success({"message": "Режим техработ включен"})
                return self._error("Не удалось включить режим техработ", status=500)

            if action == "disable_maintenance":
                success = await self.maintenance_service.disable_maintenance()
                if success:
                    return self._success({"message": "Режим техработ выключен"})
                return self._error("Не удалось выключить режим техработ", status=500)

            if action == "create_backup":
                success, message, file_path = await self.backup_service.create_backup()
                status = "ok" if success else "error"
                response = {
                    "status": status,
                    "message": message,
                }
                if file_path:
                    response["file"] = file_path
                if success:
                    return self._success(response)
                return self._error(message or "Не удалось создать бекап", status=500)

            if action == "send_daily_report":
                report_text = await self.reporting_service.send_report(ReportPeriod.DAILY)
                return self._success(
                    {
                        "message": "Отчет сформирован",
                        "preview": report_text,
                    }
                )

            return self._error("Неизвестное действие", status=400)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка выполнения действия %s", action)
            return self._error(f"Ошибка выполнения действия: {exc}", status=500)

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.Response(status=204)
