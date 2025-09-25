import json
import logging
import hmac
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import get_db
from app.database.models import (
    Subscription,
    SubscriptionStatus,
    Transaction,
    TransactionType,
    User,
    UserStatus,
)
from app.database.crud.user import get_user_by_id
from app.services.system_settings_service import bot_configuration_service
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


class AdminAPIServer:
    """HTTP API для интеграции с внешней веб-админкой."""

    def __init__(self, bot: Optional[Bot] = None) -> None:
        self.bot = bot
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

    async def create_app(self) -> web.Application:
        middlewares = [self._cors_middleware, self._auth_middleware]
        app = web.Application(middlewares=middlewares)

        app.router.add_get("/api/v1/health", self.handle_health)
        app.router.add_get("/api/v1/dashboard/summary", self.handle_dashboard_summary)

        app.router.add_get("/api/v1/users", self.handle_users_list)
        app.router.add_get("/api/v1/users/{user_id:int}", self.handle_user_detail)
        app.router.add_get(
            "/api/v1/users/{user_id:int}/transactions",
            self.handle_user_transactions,
        )
        app.router.add_post("/api/v1/users/{user_id:int}/block", self.handle_block_user)
        app.router.add_post("/api/v1/users/{user_id:int}/unblock", self.handle_unblock_user)

        app.router.add_get("/api/v1/config/categories", self.handle_config_categories)
        app.router.add_get("/api/v1/config/settings", self.handle_config_settings)
        app.router.add_get(
            "/api/v1/config/settings/{key}",
            self.handle_config_setting_detail,
        )
        app.router.add_put(
            "/api/v1/config/settings/{key}",
            self.handle_config_setting_update,
        )
        app.router.add_delete(
            "/api/v1/config/settings/{key}",
            self.handle_config_setting_reset,
        )

        self.app = app
        return app

    async def start(self) -> None:
        if not settings.ADMIN_API_ENABLED:
            logger.info("Admin API отключен настройками")
            return

        if not settings.ADMIN_API_TOKEN:
            logger.warning("ADMIN_API_TOKEN не задан. Admin API не будет запущен")
            return

        if not self.app:
            await self.create_app()

        await bot_configuration_service.initialize()

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(
            self.runner,
            host=settings.ADMIN_API_HOST,
            port=settings.ADMIN_API_PORT,
        )
        await self.site.start()

        logger.info(
            "Admin API запущен на http://%s:%s",
            settings.ADMIN_API_HOST,
            settings.ADMIN_API_PORT,
        )

    async def stop(self) -> None:
        if self.site:
            await self.site.stop()
            logger.info("Admin API остановлен")
            self.site = None

        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    # ------------------------------------------------------------------
    # Middlewares
    # ------------------------------------------------------------------
    @web.middleware
    async def _cors_middleware(
        self, request: web.Request, handler
    ) -> web.StreamResponse:
        if request.method == "OPTIONS":
            response = web.Response(status=200)
        else:
            response = await handler(request)

        allowed_origins = settings.get_admin_api_cors_origins()
        origin_header = "*" if not allowed_origins else ",".join(allowed_origins)

        response.headers["Access-Control-Allow-Origin"] = origin_header
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers[
            "Access-Control-Allow-Headers"
        ] = "Authorization, Content-Type, X-Admin-Token, X-Api-Key"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @web.middleware
    async def _auth_middleware(
        self, request: web.Request, handler
    ) -> web.StreamResponse:
        if request.method == "OPTIONS":
            return await handler(request)

        if not settings.ADMIN_API_ENABLED or not settings.ADMIN_API_TOKEN:
            return web.json_response(
                {"status": "error", "reason": "admin_api_disabled"},
                status=503,
            )

        allowed_ips = settings.get_admin_api_allowed_ips()
        if allowed_ips:
            client_ip = self._get_client_ip(request)
            if client_ip not in allowed_ips:
                logger.warning("Запрос к Admin API с запрещенного IP %s", client_ip)
                return web.json_response(
                    {"status": "error", "reason": "forbidden_ip"},
                    status=403,
                )

        provided = self._extract_token(request)
        if not provided or not hmac.compare_digest(
            str(provided), str(settings.ADMIN_API_TOKEN)
        ):
            return web.json_response(
                {"status": "error", "reason": "unauthorized"},
                status=401,
            )

        return await handler(request)

    @staticmethod
    def _extract_token(request: web.Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header.split(" ", 1)[1].strip()

        for header_name in ("X-Admin-Token", "X-Api-Key"):
            token = request.headers.get(header_name)
            if token:
                return token.strip()
        return None

    @staticmethod
    def _get_client_ip(request: web.Request) -> str:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        if request.remote:
            return request.remote
        peername = request.transport.get_extra_info("peername") if request.transport else None
        if peername:
            host, *_ = peername
            return host
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
        if not value:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @staticmethod
    def _serialize_transaction(tx: Transaction) -> Dict[str, Any]:
        return {
            "id": tx.id,
            "type": tx.type,
            "amount_kopeks": tx.amount_kopeks,
            "amount_rubles": settings.kopeks_to_rubles(tx.amount_kopeks),
            "description": tx.description,
            "payment_method": tx.payment_method,
            "external_id": tx.external_id,
            "is_completed": tx.is_completed,
            "created_at": AdminAPIServer._serialize_datetime(tx.created_at),
            "completed_at": AdminAPIServer._serialize_datetime(tx.completed_at),
        }

    @staticmethod
    def _serialize_subscription(subscription: Subscription) -> Dict[str, Any]:
        return {
            "id": subscription.id,
            "status": subscription.status,
            "actual_status": subscription.actual_status,
            "is_trial": subscription.is_trial,
            "start_date": AdminAPIServer._serialize_datetime(subscription.start_date),
            "end_date": AdminAPIServer._serialize_datetime(subscription.end_date),
            "traffic_limit_gb": subscription.traffic_limit_gb,
            "traffic_used_gb": subscription.traffic_used_gb,
            "device_limit": subscription.device_limit,
            "autopay_enabled": subscription.autopay_enabled,
            "autopay_days_before": subscription.autopay_days_before,
            "connected_squads": subscription.connected_squads,
        }

    @staticmethod
    def _serialize_user(user: User) -> Dict[str, Any]:
        subscription = getattr(user, "subscription", None)
        data = {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "status": user.status,
            "language": user.language,
            "balance_kopeks": user.balance_kopeks,
            "balance_rubles": settings.kopeks_to_rubles(user.balance_kopeks),
            "has_had_paid_subscription": user.has_had_paid_subscription,
            "created_at": AdminAPIServer._serialize_datetime(user.created_at),
            "updated_at": AdminAPIServer._serialize_datetime(user.updated_at),
            "last_activity": AdminAPIServer._serialize_datetime(user.last_activity),
            "referral_code": user.referral_code,
        }

        if subscription:
            data.update(
                {
                    "subscription_status": subscription.status,
                    "subscription_actual_status": subscription.actual_status,
                    "subscription_expires_at": AdminAPIServer._serialize_datetime(
                        subscription.end_date
                    ),
                    "subscription_is_trial": subscription.is_trial,
                }
            )
        else:
            data.update(
                {
                    "subscription_status": None,
                    "subscription_actual_status": None,
                    "subscription_expires_at": None,
                    "subscription_is_trial": None,
                }
            )

        return data

    @staticmethod
    def _normalize_incoming_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        raise ValueError("Unsupported value type")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    async def handle_health(self, request: web.Request) -> web.Response:
        payload = {
            "status": "ok",
            "timestamp": self._serialize_datetime(datetime.utcnow()),
            "services": {
                "webhooks": settings.TRIBUTE_ENABLED
                or settings.is_mulenpay_enabled()
                or settings.is_cryptobot_enabled(),
                "yookassa": settings.is_yookassa_enabled(),
                "pal24": settings.is_pal24_enabled(),
                "monitoring": getattr(monitoring_service, "is_running", None),
            },
        }
        return web.json_response(payload)

    async def handle_dashboard_summary(self, request: web.Request) -> web.Response:
        response_payload: Dict[str, Any] = {}
        async for db in get_db():
            total_users = await db.scalar(select(func.count(User.id))) or 0
            active_users = await db.scalar(
                select(func.count(User.id)).where(User.status == UserStatus.ACTIVE.value)
            ) or 0
            blocked_users = await db.scalar(
                select(func.count(User.id)).where(User.status == UserStatus.BLOCKED.value)
            ) or 0

            total_balance = await db.scalar(
                select(func.coalesce(func.sum(User.balance_kopeks), 0))
            ) or 0

            now = datetime.utcnow()
            active_subs = await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.end_date > now,
                )
            ) or 0
            trial_subs = await db.scalar(
                select(func.count(Subscription.id)).where(Subscription.is_trial.is_(True))
            ) or 0
            expired_subs = await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED.value
                )
            ) or 0

            revenue = await db.scalar(
                select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
                    Transaction.type.in_(
                        [
                            TransactionType.DEPOSIT.value,
                            TransactionType.SUBSCRIPTION_PAYMENT.value,
                        ]
                    )
                )
            ) or 0

            top_payment_methods_result = await db.execute(
                select(
                    Transaction.payment_method,
                    func.count(Transaction.id).label("count"),
                )
                .where(Transaction.payment_method.is_not(None))
                .group_by(Transaction.payment_method)
                .order_by(func.count(Transaction.id).desc())
                .limit(5)
            )
            top_payment_methods = [
                {"payment_method": row[0], "count": row[1]}
                for row in top_payment_methods_result.all()
            ]

            response_payload = {
                "users": {
                    "total": total_users,
                    "active": active_users,
                    "blocked": blocked_users,
                    "trial_subscriptions": trial_subs,
                },
                "subscriptions": {
                    "active": active_subs,
                    "expired": expired_subs,
                    "trial": trial_subs,
                },
                "finance": {
                    "total_balance_kopeks": total_balance,
                    "total_balance_rubles": settings.kopeks_to_rubles(total_balance),
                    "revenue_kopeks": revenue,
                    "revenue_rubles": settings.kopeks_to_rubles(revenue),
                    "top_payment_methods": top_payment_methods,
                },
            }
            break

        return web.json_response(response_payload)

    async def handle_users_list(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        try:
            limit = min(int(params.get("limit", 50)), 200)
            if limit <= 0:
                limit = 50
        except ValueError:
            limit = 50

        try:
            offset = max(int(params.get("offset", 0)), 0)
        except ValueError:
            offset = 0

        status_filter = params.get("status")
        search_query = params.get("search", "").strip()

        filters: List[Any] = []
        if status_filter:
            if status_filter not in {item.value for item in UserStatus}:
                return web.json_response(
                    {
                        "status": "error",
                        "reason": "invalid_status",
                        "available": [item.value for item in UserStatus],
                    },
                    status=400,
                )
            filters.append(User.status == status_filter)

        if search_query:
            like_pattern = f"%{search_query.lower()}%"
            conditions: List[Any] = [
                func.lower(User.username).like(like_pattern),
                func.lower(User.first_name).like(like_pattern),
                func.lower(User.last_name).like(like_pattern),
            ]
            if search_query.isdigit():
                search_number = int(search_query)
                conditions.append(User.id == search_number)
                conditions.append(User.telegram_id == search_number)
            filters.append(or_(*conditions))

        async for db in get_db():
            count_stmt = select(func.count(User.id))
            if filters:
                count_stmt = count_stmt.where(*filters)
            total = await db.scalar(count_stmt) or 0

            stmt = (
                select(User)
                .options(selectinload(User.subscription))
                .order_by(User.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            if filters:
                stmt = stmt.where(*filters)

            result = await db.execute(stmt)
            users = result.scalars().all()

            data = {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [self._serialize_user(user) for user in users],
            }
            return web.json_response(data)

        return web.json_response({"total": 0, "items": []})

    async def handle_user_detail(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        async for db in get_db():
            stmt = (
                select(User)
                .options(selectinload(User.subscription), selectinload(User.referrer))
                .where(User.id == user_id)
            )
            result = await db.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                return web.json_response(
                    {"status": "error", "reason": "user_not_found"},
                    status=404,
                )

            transactions_result = await db.execute(
                select(Transaction)
                .where(Transaction.user_id == user_id)
                .order_by(Transaction.created_at.desc())
                .limit(25)
            )
            transactions = transactions_result.scalars().all()

            payload: Dict[str, Any] = {
                "user": self._serialize_user(user),
                "subscription": self._serialize_subscription(user.subscription)
                if user.subscription
                else None,
                "transactions": [self._serialize_transaction(tx) for tx in transactions],
            }

            referrer = getattr(user, "referrer", None)
            if referrer:
                payload["referrer"] = {
                    "id": referrer.id,
                    "telegram_id": referrer.telegram_id,
                    "username": referrer.username,
                    "full_name": referrer.full_name,
                }
            else:
                payload["referrer"] = None

            return web.json_response(payload)

        return web.json_response(
            {"status": "error", "reason": "user_not_found"}, status=404
        )

    async def handle_user_transactions(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        params = request.rel_url.query
        try:
            limit = min(int(params.get("limit", 50)), 200)
            if limit <= 0:
                limit = 50
        except ValueError:
            limit = 50

        try:
            offset = max(int(params.get("offset", 0)), 0)
        except ValueError:
            offset = 0

        async for db in get_db():
            user = await get_user_by_id(db, user_id)
            if not user:
                return web.json_response(
                    {"status": "error", "reason": "user_not_found"},
                    status=404,
                )

            count_stmt = select(func.count(Transaction.id)).where(
                Transaction.user_id == user_id
            )
            total = await db.scalar(count_stmt) or 0

            stmt = (
                select(Transaction)
                .where(Transaction.user_id == user_id)
                .order_by(Transaction.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            result = await db.execute(stmt)
            transactions = result.scalars().all()

            payload = {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [self._serialize_transaction(tx) for tx in transactions],
            }
            return web.json_response(payload)

        return web.json_response(
            {"status": "error", "reason": "user_not_found"}, status=404
        )

    async def handle_block_user(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"status": "error", "reason": "invalid_json"}, status=400
            )

        admin_id = payload.get("admin_id")
        if not isinstance(admin_id, int):
            return web.json_response(
                {"status": "error", "reason": "admin_id_required"},
                status=400,
            )

        if not settings.is_admin(admin_id):
            return web.json_response(
                {"status": "error", "reason": "not_an_admin"}, status=403
            )

        reason = payload.get("reason") or "Заблокирован через Admin API"

        user_service = UserService()
        response_payload: Dict[str, Any]
        async for db in get_db():
            user = await get_user_by_id(db, user_id)
            if not user:
                return web.json_response(
                    {"status": "error", "reason": "user_not_found"},
                    status=404,
                )

            success = await user_service.block_user(
                db,
                user_id=user_id,
                admin_id=admin_id,
                reason=reason,
            )
            if not success:
                return web.json_response(
                    {"status": "error", "reason": "block_failed"},
                    status=400,
                )

            await db.refresh(user)
            response_payload = {
                "status": "ok",
                "user": self._serialize_user(user),
            }
            break

        return web.json_response(response_payload)

    async def handle_unblock_user(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"status": "error", "reason": "invalid_json"}, status=400
            )

        admin_id = payload.get("admin_id")
        if not isinstance(admin_id, int):
            return web.json_response(
                {"status": "error", "reason": "admin_id_required"},
                status=400,
            )

        if not settings.is_admin(admin_id):
            return web.json_response(
                {"status": "error", "reason": "not_an_admin"}, status=403
            )

        reason = payload.get("reason") or "Разблокирован через Admin API"
        user_service = UserService()

        response_payload: Dict[str, Any]
        async for db in get_db():
            user = await get_user_by_id(db, user_id)
            if not user:
                return web.json_response(
                    {"status": "error", "reason": "user_not_found"},
                    status=404,
                )

            success = await user_service.unblock_user(
                db,
                user_id=user_id,
                admin_id=admin_id,
            )
            if not success:
                return web.json_response(
                    {"status": "error", "reason": "unblock_failed"},
                    status=400,
                )

            await db.refresh(user)
            response_payload = {
                "status": "ok",
                "user": self._serialize_user(user),
                "message": reason,
            }
            break

        return web.json_response(response_payload)

    async def handle_config_categories(self, request: web.Request) -> web.Response:
        categories = bot_configuration_service.get_categories()
        payload = [
            {
                "key": key,
                "label": label,
                "settings_count": count,
            }
            for key, label, count in categories
        ]
        return web.json_response({"items": payload})

    async def handle_config_settings(self, request: web.Request) -> web.Response:
        category_key = request.rel_url.query.get("category")

        if category_key:
            definitions = bot_configuration_service.get_settings_for_category(
                category_key
            )
        else:
            definitions = bot_configuration_service.get_all_definitions()

        items = []
        for definition in definitions:
            current_value = bot_configuration_service.get_current_value(definition.key)
            items.append(
                {
                    "key": definition.key,
                    "name": definition.display_name,
                    "category_key": definition.category_key,
                    "category_label": definition.category_label,
                    "type": definition.type_label,
                    "is_optional": definition.is_optional,
                    "current_value": current_value,
                    "formatted_value": bot_configuration_service.format_value(
                        current_value
                    ),
                    "has_override": bot_configuration_service.has_override(
                        definition.key
                    ),
                }
            )

        return web.json_response({"items": items})

    async def handle_config_setting_detail(
        self, request: web.Request
    ) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response(
                {"status": "error", "reason": "setting_not_found"},
                status=404,
            )

        current_value = bot_configuration_service.get_current_value(key)
        original_value = bot_configuration_service.get_original_value(key)
        choices = bot_configuration_service.get_choice_options(key)

        payload = {
            "key": key,
            "name": definition.display_name,
            "category_key": definition.category_key,
            "category_label": definition.category_label,
            "type": definition.type_label,
            "is_optional": definition.is_optional,
            "current_value": current_value,
            "original_value": original_value,
            "formatted_current": bot_configuration_service.format_value(
                current_value
            ),
            "formatted_original": bot_configuration_service.format_value(
                original_value
            ),
            "has_override": bot_configuration_service.has_override(key),
            "choices": [
                {
                    "value": option.value,
                    "label": option.label,
                    "description": option.description,
                }
                for option in choices
            ],
        }

        return web.json_response(payload)

    async def handle_config_setting_update(
        self, request: web.Request
    ) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response(
                {"status": "error", "reason": "setting_not_found"},
                status=404,
            )

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"status": "error", "reason": "invalid_json"}, status=400
            )

        if "value" not in payload:
            return web.json_response(
                {"status": "error", "reason": "value_required"},
                status=400,
            )

        try:
            normalized_value = self._normalize_incoming_value(payload.get("value"))
            parsed_value = bot_configuration_service.parse_user_value(
                key, normalized_value
            )
        except ValueError as error:
            return web.json_response(
                {"status": "error", "reason": str(error)},
                status=400,
            )

        async for db in get_db():
            await bot_configuration_service.set_value(db, key, parsed_value)
            break

        updated = bot_configuration_service.get_setting_summary(key)
        updated["current_value"] = bot_configuration_service.get_current_value(key)
        return web.json_response({"status": "ok", "setting": updated})

    async def handle_config_setting_reset(
        self, request: web.Request
    ) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response(
                {"status": "error", "reason": "setting_not_found"},
                status=404,
            )

        async for db in get_db():
            await bot_configuration_service.reset_value(db, key)
            break

        updated = bot_configuration_service.get_setting_summary(key)
        updated["current_value"] = bot_configuration_service.get_current_value(key)
        return web.json_response({"status": "ok", "setting": updated})


# Избегаем циклических импортов
from app.services.monitoring_service import monitoring_service  # noqa: E402  # pylint: disable=wrong-import-position
