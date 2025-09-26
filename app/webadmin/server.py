"""aiohttp server exposing the bot web admin API and UI."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.promocode import (
    create_promocode,
    delete_promocode,
    get_promocode_by_code,
    get_promocode_statistics,
    get_promocodes_count,
    get_promocodes_list,
    update_promocode,
)
from app.database.crud.server_squad import (
    delete_server_squad,
    get_all_server_squads,
    get_server_squad_by_id,
    sync_with_remnawave,
    update_server_squad,
    update_server_squad_promo_groups,
)
from app.database.models import (
    PaymentMethod,
    PromoCode,
    PromoCodeType,
    ServerSquad,
    TransactionType,
)
from app.services.backup_service import BackupService
from app.services.maintenance_service import MaintenanceService
from app.services.monitoring_service import MonitoringService
from app.services.reporting_service import ReportPeriod, ReportingService
from app.services.remnawave_service import (
    RemnaWaveConfigurationError,
    RemnaWaveService,
)
from app.services.support_settings_service import SupportSettingsService
from app.services.system_settings_service import bot_configuration_service
from app.services.version_service import VersionService
from app.webadmin.dashboard import (
    collect_dashboard_summary,
    collect_revenue_series,
    fetch_recent_users,
    fetch_server_overview,
    get_user_details,
    list_transactions,
    list_users,
)
from app.webadmin.serializers import serialize_server

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

        app.router.add_get("/api/transactions", self.handle_transactions)

        app.router.add_get("/api/servers", self.handle_servers)

        app.router.add_get("/api/settings/categories", self.handle_settings_categories)
        app.router.add_get("/api/settings/category/{category_key}", self.handle_settings_category)
        app.router.add_put("/api/settings/{key}", self.handle_setting_update)
        app.router.add_delete("/api/settings/{key}", self.handle_setting_reset)

        app.router.add_post("/api/bot/control", self.handle_bot_control)

        # Remnawave management
        app.router.add_get("/api/remnawave/overview", self.handle_remnawave_overview)
        app.router.add_get("/api/remnawave/health", self.handle_remnawave_health)
        app.router.add_get("/api/remnawave/nodes", self.handle_remnawave_nodes)
        app.router.add_get("/api/remnawave/nodes/{node_uuid}", self.handle_remnawave_node_details)
        app.router.add_post("/api/remnawave/nodes/{node_uuid}/action", self.handle_remnawave_node_action)
        app.router.add_post("/api/remnawave/nodes/actions/restart-all", self.handle_remnawave_restart_all_nodes)
        app.router.add_get("/api/remnawave/squads", self.handle_remnawave_squads)
        app.router.add_get("/api/remnawave/squads/{squad_uuid}", self.handle_remnawave_squad_details)
        app.router.add_post("/api/remnawave/squads", self.handle_remnawave_create_squad)
        app.router.add_put("/api/remnawave/squads/{squad_uuid}", self.handle_remnawave_update_squad)
        app.router.add_delete("/api/remnawave/squads/{squad_uuid}", self.handle_remnawave_delete_squad)
        app.router.add_post("/api/remnawave/squads/{squad_uuid}/actions", self.handle_remnawave_squad_action)
        app.router.add_get("/api/remnawave/inbounds", self.handle_remnawave_inbounds)
        app.router.add_get("/api/remnawave/sync/recommendations", self.handle_remnawave_sync_recommendations)
        app.router.add_post("/api/remnawave/sync", self.handle_remnawave_sync)

        # Servers & promo groups management
        app.router.add_get("/api/servers/{server_id}", self.handle_server_details)
        app.router.add_put("/api/servers/{server_id}", self.handle_server_update)
        app.router.add_delete("/api/servers/{server_id}", self.handle_server_delete)
        app.router.add_post("/api/servers/{server_id}/promo-groups", self.handle_server_update_promo_groups)
        app.router.add_post("/api/servers/sync", self.handle_servers_sync)
        app.router.add_get("/api/promo-groups", self.handle_promo_groups)

        # Promocodes
        app.router.add_get("/api/promocodes", self.handle_promocodes_list)
        app.router.add_post("/api/promocodes", self.handle_promocode_create)
        app.router.add_get("/api/promocodes/{promocode_id}", self.handle_promocode_details)
        app.router.add_put("/api/promocodes/{promocode_id}", self.handle_promocode_update)
        app.router.add_post("/api/promocodes/{promocode_id}/toggle", self.handle_promocode_toggle)
        app.router.add_delete("/api/promocodes/{promocode_id}", self.handle_promocode_delete)
        app.router.add_get("/api/promocodes/{promocode_id}/stats", self.handle_promocode_stats)

        # Support settings
        app.router.add_get("/api/support/settings", self.handle_support_settings)
        app.router.add_put("/api/support/settings", self.handle_support_settings_update)
        app.router.add_get("/api/support/moderators", self.handle_support_moderators)
        app.router.add_post("/api/support/moderators", self.handle_support_add_moderator)
        app.router.add_delete(
            "/api/support/moderators/{telegram_id}", self.handle_support_remove_moderator
        )
        app.router.add_put("/api/support/info", self.handle_support_info_update)

        # Updates info
        app.router.add_get("/api/updates/check", self.handle_updates_check)
        app.router.add_get("/api/updates/info", self.handle_updates_info)

        app.router.add_options("/{tail:.*}", self.handle_options)

    def _render_index(self) -> str:
        title = settings.get_webadmin_title()
        return self._index_template.replace("{{WEBADMIN_TITLE}}", title)

    @staticmethod
    def _success(data: Any = None, *, status: int = 200, **extra: Any) -> web.Response:
        payload: Dict[str, Any] = {"status": "ok"}
        if data is not None:
            payload["data"] = data
        if extra:
            payload.update(extra)
        return web.json_response(payload, status=status)

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

    @staticmethod
    def _round_currency(kopeks: Optional[int]) -> float:
        if not kopeks:
            return 0.0
        return round(kopeks / 100, 2)

    @staticmethod
    def _serialize_promocode(promocode: PromoCode) -> Dict[str, Any]:
        return {
            "id": promocode.id,
            "code": promocode.code,
            "type": promocode.type,
            "is_active": bool(promocode.is_active),
            "max_uses": promocode.max_uses,
            "current_uses": promocode.current_uses,
            "balance_bonus_kopeks": promocode.balance_bonus_kopeks or 0,
            "balance_bonus_rub": WebAdminServer._round_currency(
                promocode.balance_bonus_kopeks
            ),
            "subscription_days": promocode.subscription_days or 0,
            "valid_until": promocode.valid_until.isoformat()
            if promocode.valid_until
            else None,
            "created_at": promocode.created_at.isoformat()
            if promocode.created_at
            else None,
            "updated_at": promocode.updated_at.isoformat()
            if promocode.updated_at
            else None,
            "description": promocode.description,
        }

    @staticmethod
    def _serialize_server_model(server: ServerSquad) -> Dict[str, Any]:
        return serialize_server(server)

    @staticmethod
    def _build_remnawave_service() -> RemnaWaveService:
        service = RemnaWaveService()
        if not service.is_configured:
            raise RemnaWaveConfigurationError(service.configuration_error or "Remnawave API не настроен")
        return service

    @staticmethod
    def _parse_promocode_type(value: str) -> PromoCodeType:
        try:
            return PromoCodeType(value)
        except ValueError as exc:
            raise ValueError("Некорректный тип промокода") from exc

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
        limit = max(1, limit)
        offset = max(0, offset)
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

    async def handle_transactions(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            limit = 20
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            offset = 0

        transaction_type = request.query.get("type")
        if transaction_type:
            try:
                transaction_type = TransactionType(transaction_type).value
            except ValueError:
                return self._error("Некорректный тип транзакции", status=400)

        status = request.query.get("status")
        if status and status not in {"completed", "pending"}:
            return self._error("Некорректный статус транзакции", status=400)

        payment_method = request.query.get("payment")
        if payment_method:
            try:
                payment_method = PaymentMethod(payment_method).value
            except ValueError:
                return self._error("Некорректный метод оплаты", status=400)

        search = request.query.get("search")
        if search:
            search = search.strip()
        else:
            search = None

        async with AsyncSessionLocal() as session:
            data = await list_transactions(
                session,
                limit=limit,
                offset=offset,
                search=search,
                transaction_type=transaction_type,
                status=status,
                payment_method=payment_method,
            )

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

    # ------------------------------------------------------------------
    # Remnawave domain
    # ------------------------------------------------------------------
    async def handle_remnawave_overview(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            data = await service.get_system_statistics()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения статистики Remnawave")
            return self._error(f"Не удалось получить статистику: {exc}", status=502)

        if not data:
            return self._error("Нет данных от Remnawave", status=502)
        if isinstance(data, dict) and data.get("error"):
            return self._error(str(data.get("error")), status=502)

        return self._success(data)

    async def handle_remnawave_health(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            data = await service.check_panel_health()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка проверки состояния панели")
            return self._error(f"Не удалось проверить состояние панели: {exc}", status=500)

        return self._success(data)

    async def handle_remnawave_nodes(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            nodes = await service.get_all_nodes()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения списка нод")
            return self._error(f"Не удалось получить ноды: {exc}", status=502)

        return self._success(nodes or [])

    async def handle_remnawave_node_details(self, request: web.Request) -> web.Response:
        node_uuid = request.match_info.get("node_uuid", "").strip()
        if not node_uuid:
            return self._error("Не указан идентификатор ноды", status=400)

        try:
            service = self._build_remnawave_service()
            details = await service.get_node_details(node_uuid)
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения информации о ноде %s", node_uuid)
            return self._error(f"Не удалось получить данные ноды: {exc}", status=502)

        if not details:
            return self._error("Нода не найдена", status=404)

        return self._success(details)

    async def handle_remnawave_node_action(self, request: web.Request) -> web.Response:
        node_uuid = request.match_info.get("node_uuid", "").strip()
        if not node_uuid:
            return self._error("Не указан идентификатор ноды", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        action = (payload.get("action") or "").strip().lower()
        if action not in {"enable", "disable", "restart"}:
            return self._error("Недопустимое действие", status=400)

        try:
            service = self._build_remnawave_service()
            success = await service.manage_node(node_uuid, action)
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка выполнения действия %s для ноды %s", action, node_uuid)
            return self._error(f"Не удалось выполнить действие: {exc}", status=500)

        if not success:
            return self._error("Не удалось выполнить действие", status=500)

        return self._success({"message": "Действие выполнено"})

    async def handle_remnawave_restart_all_nodes(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            success = await service.restart_all_nodes()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка перезагрузки всех нод")
            return self._error(f"Не удалось отправить команду: {exc}", status=500)

        if not success:
            return self._error("Не удалось перезагрузить ноды", status=500)

        return self._success({"message": "Команда отправлена"})

    async def handle_remnawave_squads(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            squads = await service.get_all_squads()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения списка сквадов")
            return self._error(f"Не удалось получить сквады: {exc}", status=502)

        return self._success(squads or [])

    async def handle_remnawave_squad_details(self, request: web.Request) -> web.Response:
        squad_uuid = request.match_info.get("squad_uuid", "").strip()
        if not squad_uuid:
            return self._error("Не указан идентификатор сквада", status=400)

        try:
            service = self._build_remnawave_service()
            details = await service.get_squad_details(squad_uuid)
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения данных сквада %s", squad_uuid)
            return self._error(f"Не удалось получить данные сквада: {exc}", status=502)

        if not details:
            return self._error("Сквад не найден", status=404)

        return self._success(details)

    async def handle_remnawave_create_squad(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        name = (payload.get("name") or "").strip()
        inbounds = payload.get("inbounds") or []
        if not name:
            return self._error("Укажите название сквада", status=400)
        if not isinstance(inbounds, list):
            return self._error("Поле inbounds должно быть списком", status=400)

        inbounds_clean = [str(item).strip() for item in inbounds if str(item).strip()]

        try:
            service = self._build_remnawave_service()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)

        try:
            async with service.get_api_client() as api:
                squad = await api.create_internal_squad(name, inbounds_clean)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка создания сквада")
            return self._error(f"Не удалось создать сквад: {exc}", status=500)

        if not squad:
            return self._error("Сквад не был создан", status=500)

        return self._success(
            {
                "uuid": getattr(squad, "uuid", None),
                "name": getattr(squad, "name", name),
                "inbounds": inbounds_clean,
            }
        )

    async def handle_remnawave_update_squad(self, request: web.Request) -> web.Response:
        squad_uuid = request.match_info.get("squad_uuid", "").strip()
        if not squad_uuid:
            return self._error("Не указан идентификатор сквада", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        new_name = payload.get("name")
        inbounds = payload.get("inbounds") if "inbounds" in payload else None

        try:
            service = self._build_remnawave_service()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)

        updates: Dict[str, Any] = {}

        if new_name is not None:
            clean_name = str(new_name).strip()
            if not clean_name:
                return self._error("Название не может быть пустым", status=400)
            try:
                renamed = await service.rename_squad(squad_uuid, clean_name)
            except AttributeError:
                renamed = await service.update_squad(squad_uuid, name=clean_name)
            if not renamed:
                return self._error("Не удалось переименовать сквад", status=500)
            updates["name"] = clean_name

        if inbounds is not None:
            if not isinstance(inbounds, list):
                return self._error("Поле inbounds должно быть списком", status=400)
            inbound_ids = [str(item).strip() for item in inbounds if str(item).strip()]
            success = await service.update_squad_inbounds(squad_uuid, inbound_ids)
            if not success:
                return self._error("Не удалось обновить инбаунды", status=500)
            updates["inbounds"] = inbound_ids

        if not updates:
            return self._error("Нет данных для обновления", status=400)

        return self._success({"updated": updates})

    async def handle_remnawave_delete_squad(self, request: web.Request) -> web.Response:
        squad_uuid = request.match_info.get("squad_uuid", "").strip()
        if not squad_uuid:
            return self._error("Не указан идентификатор сквада", status=400)

        try:
            service = self._build_remnawave_service()
            success = await service.delete_squad(squad_uuid)
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка удаления сквада %s", squad_uuid)
            return self._error(f"Не удалось удалить сквад: {exc}", status=500)

        if not success:
            return self._error("Сквад не был удален", status=500)

        return self._success({"deleted": True})

    async def handle_remnawave_squad_action(self, request: web.Request) -> web.Response:
        squad_uuid = request.match_info.get("squad_uuid", "").strip()
        if not squad_uuid:
            return self._error("Не указан идентификатор сквада", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        action = (payload.get("action") or "").strip().lower()
        if action not in {"add_all_users", "remove_all_users"}:
            return self._error("Неизвестное действие", status=400)

        try:
            service = self._build_remnawave_service()
            if action == "add_all_users":
                success = await service.add_all_users_to_squad(squad_uuid)
            else:
                success = await service.remove_all_users_from_squad(squad_uuid)
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка действия %s для сквада %s", action, squad_uuid)
            return self._error(f"Не удалось выполнить действие: {exc}", status=500)

        if not success:
            return self._error("Действие не выполнено", status=500)

        return self._success({"message": "Действие выполнено"})

    async def handle_remnawave_inbounds(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            inbounds = await service.get_all_inbounds()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения списка инбаундов")
            return self._error(f"Не удалось получить инбаунды: {exc}", status=500)

        return self._success(inbounds or [])

    async def handle_remnawave_sync_recommendations(
        self, request: web.Request
    ) -> web.Response:
        try:
            service = self._build_remnawave_service()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)

        async with AsyncSessionLocal() as session:
            try:
                recommendations = await service.get_sync_recommendations(session)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка получения рекомендаций синхронизации")
                return self._error(f"Не удалось получить рекомендации: {exc}", status=500)

        return self._success(recommendations or {})

    async def handle_remnawave_sync(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        action = (payload.get("action") or "").strip().lower()
        if not action:
            return self._error("Не указано действие", status=400)

        try:
            service = self._build_remnawave_service()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)

        async with AsyncSessionLocal() as session:
            try:
                if action == "from_panel_all":
                    result = await service.sync_users_from_panel(session, "all")
                elif action == "from_panel_new":
                    result = await service.sync_users_from_panel(session, "new")
                elif action == "from_panel_update":
                    result = await service.sync_users_from_panel(session, "update")
                elif action == "to_panel":
                    result = await service.sync_users_to_panel(session)
                elif action == "validate":
                    result = await service.validate_and_fix_subscriptions(session)
                elif action == "cleanup":
                    result = await service.cleanup_orphaned_subscriptions(session)
                elif action == "sync_statuses":
                    result = await service.sync_subscription_statuses(session)
                else:
                    return self._error("Неизвестное действие", status=400)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка выполнения синхронизации %s", action)
                return self._error(f"Не удалось выполнить синхронизацию: {exc}", status=500)

        return self._success({"action": action, "result": result})

    # ------------------------------------------------------------------
    # Servers management
    # ------------------------------------------------------------------
    async def handle_server_details(self, request: web.Request) -> web.Response:
        try:
            server_id = int(request.match_info.get("server_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор сервера", status=400)

        async with AsyncSessionLocal() as session:
            server = await get_server_squad_by_id(session, server_id)

        if not server:
            return self._error("Сервер не найден", status=404)

        return self._success(self._serialize_server_model(server))

    async def handle_server_update(self, request: web.Request) -> web.Response:
        try:
            server_id = int(request.match_info.get("server_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор сервера", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        updates: Dict[str, Any] = {}

        if "display_name" in payload:
            updates["display_name"] = str(payload.get("display_name") or "").strip()
            if not updates["display_name"]:
                return self._error("Название не может быть пустым", status=400)

        if "country_code" in payload:
            updates["country_code"] = (
                str(payload.get("country_code") or "").strip().upper() or None
            )

        if "description" in payload:
            description = payload.get("description")
            updates["description"] = str(description).strip() if description else None

        if "max_users" in payload:
            try:
                max_users = payload.get("max_users")
                if max_users is None or max_users == "":
                    updates["max_users"] = None
                else:
                    updates["max_users"] = max(0, int(max_users))
            except (TypeError, ValueError):
                return self._error("Некорректное значение max_users", status=400)

        if "price_kopeks" in payload or "price_rub" in payload:
            if "price_kopeks" in payload:
                try:
                    price_kopeks = int(payload.get("price_kopeks"))
                except (TypeError, ValueError):
                    return self._error("Некорректная цена", status=400)
            else:
                try:
                    price_rub = float(payload.get("price_rub"))
                except (TypeError, ValueError):
                    return self._error("Некорректная цена", status=400)
                price_kopeks = int(round(price_rub * 100))
            updates["price_kopeks"] = max(0, price_kopeks)

        if "is_available" in payload:
            updates["is_available"] = bool(payload.get("is_available"))

        if "sort_order" in payload:
            try:
                updates["sort_order"] = int(payload.get("sort_order"))
            except (TypeError, ValueError):
                return self._error("Некорректный sort_order", status=400)

        if not updates:
            return self._error("Нет данных для обновления", status=400)

        async with AsyncSessionLocal() as session:
            updated = await update_server_squad(session, server_id, **updates)

        if not updated:
            return self._error("Не удалось обновить сервер", status=500)

        return self._success(self._serialize_server_model(updated))

    async def handle_server_delete(self, request: web.Request) -> web.Response:
        try:
            server_id = int(request.match_info.get("server_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор сервера", status=400)

        async with AsyncSessionLocal() as session:
            success = await delete_server_squad(session, server_id)

        if not success:
            return self._error("Не удалось удалить сервер (возможно, есть активные подключения)", status=400)

        return self._success({"deleted": True})

    async def handle_server_update_promo_groups(
        self, request: web.Request
    ) -> web.Response:
        try:
            server_id = int(request.match_info.get("server_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор сервера", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        promo_group_ids = payload.get("promo_group_ids")
        if not isinstance(promo_group_ids, list) or not promo_group_ids:
            return self._error("Укажите список промогрупп", status=400)

        try:
            normalized_ids = [int(pg_id) for pg_id in promo_group_ids]
        except (TypeError, ValueError):
            return self._error("Некорректные идентификаторы промогрупп", status=400)

        async with AsyncSessionLocal() as session:
            try:
                updated = await update_server_squad_promo_groups(
                    session, server_id, normalized_ids
                )
            except ValueError as exc:
                return self._error(str(exc), status=400)

        if not updated:
            return self._error("Не удалось обновить промогруппы", status=500)

        return self._success(self._serialize_server_model(updated))

    async def handle_servers_sync(self, request: web.Request) -> web.Response:
        try:
            service = self._build_remnawave_service()
            squads = await service.get_all_squads()
        except RemnaWaveConfigurationError as exc:
            return self._error(str(exc), status=503)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка получения сквадов для синхронизации серверов")
            return self._error(f"Не удалось получить данные Remnawave: {exc}", status=502)

        async with AsyncSessionLocal() as session:
            created = updated = disabled = 0
            if squads:
                created, updated, disabled = await sync_with_remnawave(session, squads)

        summary = {
            "total": len(squads or []),
            "created": created,
            "updated": updated,
            "disabled": disabled,
        }
        return self._success(summary)

    async def handle_promo_groups(self, request: web.Request) -> web.Response:
        async with AsyncSessionLocal() as session:
            groups = await get_promo_groups_with_counts(session)

        items = [
            {
                "id": group.id,
                "name": group.name,
                "is_default": bool(group.is_default),
                "server_discount_percent": group.server_discount_percent,
                "traffic_discount_percent": group.traffic_discount_percent,
                "device_discount_percent": group.device_discount_percent,
                "members_count": count,
            }
            for group, count in groups
        ]

        return self._success(items)

    # ------------------------------------------------------------------
    # Promocodes
    # ------------------------------------------------------------------
    async def handle_promocodes_list(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "20"))
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            return self._error("Некорректные параметры пагинации", status=400)

        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        is_active_raw = request.query.get("is_active")
        is_active: Optional[bool] = None
        if is_active_raw is not None:
            if is_active_raw.lower() in {"true", "1"}:
                is_active = True
            elif is_active_raw.lower() in {"false", "0"}:
                is_active = False
            else:
                return self._error("Некорректное значение is_active", status=400)

        async with AsyncSessionLocal() as session:
            items = await get_promocodes_list(
                session, offset=offset, limit=limit, is_active=is_active
            )
            total = await get_promocodes_count(session, is_active=is_active)

        data = [self._serialize_promocode(item) for item in items]
        return self._success({"items": data, "total": total, "offset": offset, "limit": limit})

    async def handle_promocode_create(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        code = (payload.get("code") or "").strip().upper()
        type_raw = (payload.get("type") or "").strip().lower()
        if not code or not type_raw:
            return self._error("Укажите код и тип промокода", status=400)

        try:
            promo_type = self._parse_promocode_type(type_raw)
        except ValueError as exc:
            return self._error(str(exc), status=400)

        try:
            balance_bonus_kopeks = int(payload.get("balance_bonus_kopeks") or 0)
        except (TypeError, ValueError):
            return self._error("Некорректный бонус", status=400)
        if "balance_bonus_rub" in payload:
            try:
                balance_bonus_kopeks = int(round(float(payload["balance_bonus_rub"]) * 100))
            except (TypeError, ValueError):
                return self._error("Некорректный бонус", status=400)

        try:
            subscription_days = int(payload.get("subscription_days") or 0)
        except (TypeError, ValueError):
            return self._error("Некорректное количество дней", status=400)
        try:
            max_uses = max(1, int(payload.get("max_uses") or 1))
        except (TypeError, ValueError):
            return self._error("Некорректное значение max_uses", status=400)

        valid_until_raw = payload.get("valid_until")
        valid_until: Optional[datetime] = None
        if valid_until_raw:
            try:
                valid_until = datetime.fromisoformat(str(valid_until_raw))
            except ValueError:
                return self._error("Некорректная дата valid_until", status=400)

        async with AsyncSessionLocal() as session:
            existing = await get_promocode_by_code(session, code)
            if existing:
                return self._error("Такой промокод уже существует", status=400)

            promocode = await create_promocode(
                session,
                code=code,
                type=promo_type,
                balance_bonus_kopeks=balance_bonus_kopeks,
                subscription_days=subscription_days,
                max_uses=max_uses,
                valid_until=valid_until,
            )

        return self._success(self._serialize_promocode(promocode), status=201)

    async def handle_promocode_details(self, request: web.Request) -> web.Response:
        try:
            promocode_id = int(request.match_info.get("promocode_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор промокода", status=400)

        async with AsyncSessionLocal() as session:
            promo = await session.get(PromoCode, promocode_id)

        if not promo:
            return self._error("Промокод не найден", status=404)

        return self._success(self._serialize_promocode(promo))

    async def handle_promocode_update(self, request: web.Request) -> web.Response:
        try:
            promocode_id = int(request.match_info.get("promocode_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор промокода", status=400)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        async with AsyncSessionLocal() as session:
            promocode = await session.get(PromoCode, promocode_id)
            if not promocode:
                return self._error("Промокод не найден", status=404)

            updates: Dict[str, Any] = {}
            if "code" in payload:
                code = (payload.get("code") or "").strip().upper()
                if not code:
                    return self._error("Код не может быть пустым", status=400)
                updates["code"] = code

            if "type" in payload:
                try:
                    updates["type"] = self._parse_promocode_type(str(payload.get("type")).strip()).value
                except ValueError as exc:
                    return self._error(str(exc), status=400)

            if "balance_bonus_kopeks" in payload or "balance_bonus_rub" in payload:
                if "balance_bonus_kopeks" in payload:
                    try:
                        updates["balance_bonus_kopeks"] = int(payload.get("balance_bonus_kopeks") or 0)
                    except (TypeError, ValueError):
                        return self._error("Некорректный бонус", status=400)
                else:
                    try:
                        updates["balance_bonus_kopeks"] = int(round(float(payload.get("balance_bonus_rub")) * 100))
                    except (TypeError, ValueError):
                        return self._error("Некорректный бонус", status=400)

            if "subscription_days" in payload:
                try:
                    updates["subscription_days"] = int(payload.get("subscription_days") or 0)
                except (TypeError, ValueError):
                    return self._error("Некорректное количество дней", status=400)

            if "max_uses" in payload:
                try:
                    updates["max_uses"] = max(1, int(payload.get("max_uses")))
                except (TypeError, ValueError):
                    return self._error("Некорректное значение max_uses", status=400)

            if "valid_until" in payload:
                raw = payload.get("valid_until")
                if raw in (None, ""):
                    updates["valid_until"] = None
                else:
                    try:
                        updates["valid_until"] = datetime.fromisoformat(str(raw))
                    except ValueError:
                        return self._error("Некорректная дата", status=400)

            if "is_active" in payload:
                updates["is_active"] = bool(payload.get("is_active"))

            if not updates:
                return self._error("Нет данных для обновления", status=400)

            updated = await update_promocode(session, promocode, **updates)

        return self._success(self._serialize_promocode(updated))

    async def handle_promocode_toggle(self, request: web.Request) -> web.Response:
        try:
            promocode_id = int(request.match_info.get("promocode_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор промокода", status=400)

        async with AsyncSessionLocal() as session:
            promocode = await session.get(PromoCode, promocode_id)
            if not promocode:
                return self._error("Промокод не найден", status=404)
            promocode.is_active = not bool(promocode.is_active)
            updated = await update_promocode(session, promocode)

        return self._success(self._serialize_promocode(updated))

    async def handle_promocode_delete(self, request: web.Request) -> web.Response:
        try:
            promocode_id = int(request.match_info.get("promocode_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор промокода", status=400)

        async with AsyncSessionLocal() as session:
            promocode = await session.get(PromoCode, promocode_id)
            if not promocode:
                return self._error("Промокод не найден", status=404)
            success = await delete_promocode(session, promocode)

        if not success:
            return self._error("Не удалось удалить промокод", status=500)

        return self._success({"deleted": True})

    async def handle_promocode_stats(self, request: web.Request) -> web.Response:
        try:
            promocode_id = int(request.match_info.get("promocode_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор промокода", status=400)

        async with AsyncSessionLocal() as session:
            promo = await session.get(PromoCode, promocode_id)
            if not promo:
                return self._error("Промокод не найден", status=404)
            stats = await get_promocode_statistics(session, promocode_id)

        recent = []
        for use in stats.get("recent_uses", []):
            recent.append(
                {
                    "id": use.id,
                    "used_at": use.used_at.isoformat() if use.used_at else None,
                    "user_id": use.user_id,
                    "user_full_name": getattr(use, "user_full_name", None),
                    "user_username": getattr(use, "user_username", None),
                    "user_telegram_id": getattr(use, "user_telegram_id", None),
                }
            )

        return self._success(
            {
                "total_uses": stats.get("total_uses", 0),
                "today_uses": stats.get("today_uses", 0),
                "recent": recent,
            }
        )

    # ------------------------------------------------------------------
    # Support settings
    # ------------------------------------------------------------------
    async def handle_support_settings(self, request: web.Request) -> web.Response:
        data = {
            "system_mode": SupportSettingsService.get_system_mode(),
            "menu_enabled": SupportSettingsService.is_support_menu_enabled(),
            "admin_ticket_notifications": SupportSettingsService.get_admin_ticket_notifications_enabled(),
            "user_ticket_notifications": SupportSettingsService.get_user_ticket_notifications_enabled(),
            "sla_enabled": SupportSettingsService.get_sla_enabled(),
            "sla_minutes": SupportSettingsService.get_sla_minutes(),
        }
        return self._success(data)

    async def handle_support_settings_update(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        updates: Dict[str, Any] = {}

        if "system_mode" in payload:
            mode = (payload.get("system_mode") or "").strip().lower()
            if not SupportSettingsService.set_system_mode(mode):
                return self._error("Некорректный режим системы", status=400)
            updates["system_mode"] = SupportSettingsService.get_system_mode()

        if "menu_enabled" in payload:
            SupportSettingsService.set_support_menu_enabled(bool(payload.get("menu_enabled")))
            updates["menu_enabled"] = SupportSettingsService.is_support_menu_enabled()

        if "admin_ticket_notifications" in payload:
            SupportSettingsService.set_admin_ticket_notifications_enabled(
                bool(payload.get("admin_ticket_notifications"))
            )
            updates["admin_ticket_notifications"] = (
                SupportSettingsService.get_admin_ticket_notifications_enabled()
            )

        if "user_ticket_notifications" in payload:
            SupportSettingsService.set_user_ticket_notifications_enabled(
                bool(payload.get("user_ticket_notifications"))
            )
            updates["user_ticket_notifications"] = (
                SupportSettingsService.get_user_ticket_notifications_enabled()
            )

        if "sla_enabled" in payload:
            SupportSettingsService.set_sla_enabled(bool(payload.get("sla_enabled")))
            updates["sla_enabled"] = SupportSettingsService.get_sla_enabled()

        if "sla_minutes" in payload:
            try:
                minutes = int(payload.get("sla_minutes"))
            except (TypeError, ValueError):
                return self._error("Некорректное значение SLA", status=400)
            if not SupportSettingsService.set_sla_minutes(minutes):
                return self._error("Некорректное значение SLA", status=400)
            updates["sla_minutes"] = SupportSettingsService.get_sla_minutes()

        if not updates:
            return self._error("Нет данных для обновления", status=400)

        return self._success(updates)

    async def handle_support_moderators(self, request: web.Request) -> web.Response:
        moderators = SupportSettingsService.get_moderators()
        return self._success({"moderators": moderators})

    async def handle_support_add_moderator(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        telegram_id = payload.get("telegram_id")
        try:
            tid = int(telegram_id)
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор", status=400)

        if not SupportSettingsService.add_moderator(tid):
            return self._error("Не удалось добавить модератора", status=500)

        return self._success({"moderators": SupportSettingsService.get_moderators()})

    async def handle_support_remove_moderator(self, request: web.Request) -> web.Response:
        try:
            telegram_id = int(request.match_info.get("telegram_id"))
        except (TypeError, ValueError):
            return self._error("Некорректный идентификатор", status=400)

        if not SupportSettingsService.remove_moderator(telegram_id):
            return self._error("Не удалось удалить модератора", status=500)

        return self._success({"moderators": SupportSettingsService.get_moderators()})

    async def handle_support_info_update(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._error("Некорректный JSON", status=400)

        language = (payload.get("language") or settings.DEFAULT_LANGUAGE).strip().lower()
        text = payload.get("text") or ""
        if not SupportSettingsService.set_support_info_text(language, text):
            return self._error("Не удалось обновить текст", status=500)

        return self._success({"language": language, "text": text})

    # ------------------------------------------------------------------
    # Updates information
    # ------------------------------------------------------------------
    async def handle_updates_check(self, request: web.Request) -> web.Response:
        force_raw = request.query.get("force", "false").lower()
        force = force_raw in {"true", "1", "yes"}
        has_updates, releases = await self.version_service.check_for_updates(force=force)
        data = {
            "current_version": self.version_service.current_version,
            "has_updates": has_updates,
            "releases": [
                {
                    "tag": release.tag_name,
                    "name": release.name,
                    "published_at": release.published_at.isoformat(),
                    "prerelease": release.prerelease,
                    "description": release.short_description,
                }
                for release in releases
            ],
        }
        return self._success(data)

    async def handle_updates_info(self, request: web.Request) -> web.Response:
        info = await self.version_service.get_version_info()
        return self._success(info)

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.Response(status=204)
