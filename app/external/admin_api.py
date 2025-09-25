import logging
import secrets
from typing import Any, Dict, List, Mapping, Optional, Tuple

from aiohttp import web
from sqlalchemy import String, and_, cast, desc, func, or_, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import (
    CryptoBotPayment,
    MulenPayPayment,
    Pal24Payment,
    PromoGroup,
    ServerSquad,
    Subscription,
    SubscriptionStatus,
    Ticket,
    TicketMessage,
    TicketStatus,
    Transaction,
    TransactionType,
    User,
    UserStatus,
    YooKassaPayment,
)
from app.services.system_settings_service import bot_configuration_service


class AdminAPIServer:
    """HTTP API сервер для интеграции веб-админки."""

    def __init__(self) -> None:
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._logger = logging.getLogger(__name__)
        self._allowed_ips = settings.get_admin_api_allowed_ips()
        self._cors_origins = settings.get_admin_api_cors_origins()
        self._skip_db_paths = {"/admin/api/health"}

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _dt_to_iso(value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        try:
            return value.isoformat()
        except AttributeError:
            return str(value)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): AdminAPIServer._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [AdminAPIServer._jsonable(item) for item in value]
        return str(value)

    @staticmethod
    def _extract_token(request: web.Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        token = request.headers.get("X-Admin-Token")
        if token:
            return token.strip()
        return None

    def _get_request_ip(self, request: web.Request) -> Optional[str]:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.remote

    def _is_ip_allowed(self, remote: Optional[str]) -> bool:
        if not self._allowed_ips:
            return True
        if remote is None:
            return False
        for pattern in self._allowed_ips:
            if pattern == "*":
                return True
            if pattern.endswith("*") and remote.startswith(pattern[:-1]):
                return True
            if remote == pattern:
                return True
        return False

    def _json_response(
        self,
        data: Optional[Dict[str, Any]] = None,
        *,
        meta: Optional[Dict[str, Any]] = None,
        status: int = 200,
    ) -> web.Response:
        payload: Dict[str, Any] = {"status": "ok"}
        if data is not None:
            payload["data"] = self._jsonable(data)
        if meta is not None:
            payload["meta"] = self._jsonable(meta)
        return web.json_response(payload, status=status)

    def _json_error(
        self,
        message: str,
        *,
        status: int = 400,
        code: Optional[str] = None,
    ) -> web.Response:
        payload: Dict[str, Any] = {"status": "error", "message": message}
        if code:
            payload["code"] = code
        return web.json_response(payload, status=status)

    @staticmethod
    def _pagination(params: Mapping[str, str]) -> Tuple[int, int]:
        try:
            page = int(params.get("page", "1"))
        except ValueError:
            page = 1
        try:
            page_size = int(params.get("page_size", "25"))
        except ValueError:
            page_size = 25
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        return page, page_size

    # ------------------------------------------------------------------
    # Инициализация приложения и middlewares
    # ------------------------------------------------------------------

    def _create_app(self) -> web.Application:
        app = web.Application()

        @web.middleware
        async def error_middleware(request: web.Request, handler):
            try:
                return await handler(request)
            except web.HTTPException:
                raise
            except Exception as error:  # pragma: no cover - защита от неожиданных ошибок
                self._logger.exception("Admin API handler error: %s", error)
                return self._json_error("internal_error", status=500, code="internal_error")

        @web.middleware
        async def cors_middleware(request: web.Request, handler):
            if request.method == "OPTIONS":
                response = web.Response(status=200)
            else:
                response = await handler(request)

            origin = request.headers.get("Origin")
            allow_all = "*" in self._cors_origins
            if allow_all and origin:
                response.headers["Access-Control-Allow-Origin"] = origin
            elif allow_all:
                response.headers["Access-Control-Allow-Origin"] = "*"
            elif origin and origin in self._cors_origins:
                response.headers["Access-Control-Allow-Origin"] = origin

            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Admin-Token"
            )
            response.headers["Access-Control-Allow-Methods"] = (
                "GET,POST,PUT,PATCH,DELETE,OPTIONS"
            )
            response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        @web.middleware
        async def auth_middleware(request: web.Request, handler):
            if request.path == "/admin/api/health":
                return await handler(request)

            if not settings.ADMIN_API_TOKEN:
                return self._json_error("admin_api_token_not_configured", status=503)

            remote_ip = self._get_request_ip(request)
            if not self._is_ip_allowed(remote_ip):
                return self._json_error("forbidden", status=403, code="ip_forbidden")

            token = self._extract_token(request)
            if not token or not secrets.compare_digest(token, settings.ADMIN_API_TOKEN):
                return self._json_error("unauthorized", status=401, code="invalid_token")

            return await handler(request)

        @web.middleware
        async def db_session_middleware(request: web.Request, handler):
            if request.path in self._skip_db_paths or request.method == "OPTIONS":
                return await handler(request)

            async with AsyncSessionLocal() as session:
                try:
                    request["db"] = session
                    response = await handler(request)
                    await session.commit()
                    return response
                except Exception:
                    await session.rollback()
                    raise

        app.middlewares.extend(
            [error_middleware, cors_middleware, auth_middleware, db_session_middleware]
        )

        return app

    def _setup_routes(self, app: web.Application) -> None:
        app.router.add_get("/admin/api/health", self.handle_health)
        app.router.add_get("/admin/api/overview", self.handle_overview)
        app.router.add_get("/admin/api/users", self.handle_users)
        app.router.add_get("/admin/api/users/{user_id:\\d+}", self.handle_user_detail)
        app.router.add_get(
            "/admin/api/users/{user_id:\\d+}/subscription",
            self.handle_user_subscription,
        )
        app.router.add_get(
            "/admin/api/users/{user_id:\\d+}/transactions",
            self.handle_user_transactions,
        )
        app.router.add_get("/admin/api/subscriptions", self.handle_subscriptions)
        app.router.add_get("/admin/api/transactions", self.handle_transactions)
        app.router.add_get("/admin/api/payments/{provider}", self.handle_payments)
        app.router.add_get("/admin/api/tickets", self.handle_tickets)
        app.router.add_get("/admin/api/tickets/{ticket_id:\\d+}", self.handle_ticket_detail)
        app.router.add_get("/admin/api/promo-groups", self.handle_promo_groups)
        app.router.add_get("/admin/api/server-squads", self.handle_server_squads)
        app.router.add_get("/admin/api/config/categories", self.handle_config_categories)
        app.router.add_get("/admin/api/config/settings", self.handle_config_settings)
        app.router.add_get(
            "/admin/api/config/settings/{key}", self.handle_config_setting_detail
        )
        app.router.add_put(
            "/admin/api/config/settings/{key}", self.handle_config_setting_update
        )
        app.router.add_delete(
            "/admin/api/config/settings/{key}", self.handle_config_setting_reset
        )

    # ------------------------------------------------------------------
    # Публичные методы запуска/остановки
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not settings.ADMIN_API_ENABLED:
            self._logger.info("Admin API отключен настройками")
            return

        if not settings.ADMIN_API_TOKEN:
            raise RuntimeError("ADMIN_API_TOKEN должен быть задан при включенном ADMIN_API_ENABLED")

        if self._app is None:
            self._app = self._create_app()
            self._setup_routes(self._app)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        host = settings.ADMIN_API_HOST
        port = settings.ADMIN_API_PORT

        self._site = web.TCPSite(self._runner, host=host, port=port)
        await self._site.start()

        origins_display = ", ".join(self._cors_origins) if self._cors_origins else "*"
        ips_display = ", ".join(self._allowed_ips) if self._allowed_ips else "*"

        self._logger.info(
            "Admin API запущен на %s:%s (origins: %s, allowed_ips: %s)",
            host,
            port,
            origins_display,
            ips_display,
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        self._logger.info("Admin API остановлен")

    # ------------------------------------------------------------------
    # Сериализация сущностей
    # ------------------------------------------------------------------

    def _serialize_user(self, user: User, *, include_subscription: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "status": user.status,
            "language": user.language,
            "balance_kopeks": user.balance_kopeks,
            "balance_rubles": user.balance_rubles,
            "has_had_paid_subscription": user.has_had_paid_subscription,
            "remnawave_uuid": user.remnawave_uuid,
            "created_at": self._dt_to_iso(user.created_at),
            "updated_at": self._dt_to_iso(user.updated_at),
            "last_activity": self._dt_to_iso(user.last_activity),
            "promo_group": None,
            "lifetime_used_traffic_bytes": user.lifetime_used_traffic_bytes,
            "auto_promo_group_assigned": user.auto_promo_group_assigned,
            "auto_promo_group_threshold_kopeks": user.auto_promo_group_threshold_kopeks,
            "referral_code": user.referral_code,
            "referred_by_id": user.referred_by_id,
        }

        if user.promo_group:
            data["promo_group"] = self._serialize_promo_group(user.promo_group, include_servers=False)

        if include_subscription and user.subscription:
            data["subscription"] = self._serialize_subscription(user.subscription)
        elif include_subscription:
            data["subscription"] = None

        return data

    def _serialize_subscription(self, subscription: Subscription) -> Dict[str, Any]:
        return {
            "id": subscription.id,
            "user_id": subscription.user_id,
            "status": subscription.status,
            "is_trial": subscription.is_trial,
            "start_date": self._dt_to_iso(subscription.start_date),
            "end_date": self._dt_to_iso(subscription.end_date),
            "traffic_limit_gb": subscription.traffic_limit_gb,
            "traffic_used_gb": subscription.traffic_used_gb,
            "device_limit": subscription.device_limit,
            "autopay_enabled": subscription.autopay_enabled,
            "autopay_days_before": subscription.autopay_days_before,
            "subscription_url": subscription.subscription_url,
            "subscription_crypto_link": subscription.subscription_crypto_link,
            "connected_squads": subscription.connected_squads or [],
            "remnawave_short_uuid": subscription.remnawave_short_uuid,
            "created_at": self._dt_to_iso(subscription.created_at),
            "updated_at": self._dt_to_iso(subscription.updated_at),
        }

    def _serialize_transaction(self, transaction: Transaction) -> Dict[str, Any]:
        return {
            "id": transaction.id,
            "user_id": transaction.user_id,
            "type": transaction.type,
            "amount_kopeks": transaction.amount_kopeks,
            "amount_rubles": transaction.amount_rubles,
            "description": transaction.description,
            "payment_method": transaction.payment_method,
            "external_id": transaction.external_id,
            "is_completed": transaction.is_completed,
            "created_at": self._dt_to_iso(transaction.created_at),
            "completed_at": self._dt_to_iso(transaction.completed_at),
        }

    @staticmethod
    def _payment_status(payment: Any) -> str:
        return getattr(payment, "status", "")

    def _serialize_payment(self, method: str, payment: Any) -> Dict[str, Any]:
        base = {
            "id": payment.id,
            "user_id": payment.user_id,
            "amount_kopeks": getattr(payment, "amount_kopeks", None),
            "created_at": self._dt_to_iso(getattr(payment, "created_at", None)),
            "updated_at": self._dt_to_iso(getattr(payment, "updated_at", None)),
            "status": self._payment_status(payment),
            "method": method,
        }

        if hasattr(payment, "amount_rubles"):
            base["amount_rubles"] = payment.amount_rubles

        additional_fields = {}
        if isinstance(payment, YooKassaPayment):
            additional_fields = {
                "yookassa_payment_id": payment.yookassa_payment_id,
                "currency": payment.currency,
                "description": payment.description,
                "is_paid": payment.is_paid,
                "is_captured": payment.is_captured,
                "confirmation_url": payment.confirmation_url,
                "payment_method_type": payment.payment_method_type,
                "refundable": payment.refundable,
                "test_mode": payment.test_mode,
                "yookassa_created_at": self._dt_to_iso(payment.yookassa_created_at),
                "captured_at": self._dt_to_iso(payment.captured_at),
            }
        elif isinstance(payment, CryptoBotPayment):
            additional_fields = {
                "invoice_id": payment.invoice_id,
                "amount": payment.amount,
                "asset": payment.asset,
                "description": payment.description,
                "payload": payment.payload,
                "bot_invoice_url": payment.bot_invoice_url,
                "mini_app_invoice_url": payment.mini_app_invoice_url,
                "web_app_invoice_url": payment.web_app_invoice_url,
                "paid_at": self._dt_to_iso(payment.paid_at),
            }
        elif isinstance(payment, MulenPayPayment):
            additional_fields = {
                "mulen_payment_id": payment.mulen_payment_id,
                "uuid": payment.uuid,
                "currency": payment.currency,
                "description": payment.description,
                "is_paid": payment.is_paid,
                "paid_at": self._dt_to_iso(payment.paid_at),
                "payment_url": payment.payment_url,
                "status": payment.status,
            }
        elif isinstance(payment, Pal24Payment):
            additional_fields = {
                "bill_id": payment.bill_id,
                "order_id": payment.order_id,
                "currency": payment.currency,
                "description": payment.description,
                "type": payment.type,
                "is_active": payment.is_active,
                "is_paid": payment.is_paid,
                "paid_at": self._dt_to_iso(payment.paid_at),
                "payment_status": payment.payment_status,
                "payment_method": payment.payment_method,
                "link_url": payment.link_url,
                "link_page_url": payment.link_page_url,
                "expires_at": self._dt_to_iso(payment.expires_at),
            }

        base.update({key: self._jsonable(value) for key, value in additional_fields.items()})
        return base

    def _serialize_ticket(self, ticket: Ticket, *, include_messages: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": ticket.id,
            "user_id": ticket.user_id,
            "title": ticket.title,
            "status": ticket.status,
            "priority": ticket.priority,
            "created_at": self._dt_to_iso(ticket.created_at),
            "updated_at": self._dt_to_iso(ticket.updated_at),
            "closed_at": self._dt_to_iso(ticket.closed_at),
            "last_sla_reminder_at": self._dt_to_iso(ticket.last_sla_reminder_at),
            "user_reply_block_permanent": ticket.user_reply_block_permanent,
            "user_reply_block_until": self._dt_to_iso(ticket.user_reply_block_until),
        }

        if ticket.user:
            data["user"] = self._serialize_user(ticket.user)

        if include_messages:
            messages = sorted(ticket.messages, key=lambda message: message.id)
            data["messages"] = [self._serialize_ticket_message(message) for message in messages]

        data["messages_count"] = len(ticket.messages) if ticket.messages else 0
        return data

    def _serialize_ticket_message(self, message: TicketMessage) -> Dict[str, Any]:
        return {
            "id": message.id,
            "ticket_id": message.ticket_id,
            "user_id": message.user_id,
            "is_from_admin": message.is_from_admin,
            "message_text": message.message_text,
            "has_media": message.has_media,
            "media_type": message.media_type,
            "media_file_id": message.media_file_id,
            "media_caption": message.media_caption,
            "created_at": self._dt_to_iso(message.created_at),
        }

    def _serialize_promo_group(
        self, promo_group: PromoGroup, *, include_servers: bool = True
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": promo_group.id,
            "name": promo_group.name,
            "server_discount_percent": promo_group.server_discount_percent,
            "traffic_discount_percent": promo_group.traffic_discount_percent,
            "device_discount_percent": promo_group.device_discount_percent,
            "period_discounts": self._jsonable(promo_group.period_discounts or {}),
            "auto_assign_total_spent_kopeks": promo_group.auto_assign_total_spent_kopeks,
            "apply_discounts_to_addons": promo_group.apply_discounts_to_addons,
            "is_default": promo_group.is_default,
            "created_at": self._dt_to_iso(promo_group.created_at),
            "updated_at": self._dt_to_iso(promo_group.updated_at),
        }

        if include_servers:
            data["server_squads"] = [
                self._serialize_server_squad(server, include_promo_groups=False)
                for server in promo_group.server_squads
            ]

        return data

    def _serialize_server_squad(
        self, squad: ServerSquad, *, include_promo_groups: bool = True
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": squad.id,
            "squad_uuid": squad.squad_uuid,
            "display_name": squad.display_name,
            "original_name": squad.original_name,
            "country_code": squad.country_code,
            "is_available": squad.is_available,
            "price_kopeks": squad.price_kopeks,
            "price_rubles": squad.price_rubles,
            "description": squad.description,
            "sort_order": squad.sort_order,
            "max_users": squad.max_users,
            "current_users": squad.current_users,
            "created_at": self._dt_to_iso(squad.created_at),
            "updated_at": self._dt_to_iso(squad.updated_at),
            "is_full": squad.is_full,
            "availability_status": squad.availability_status,
        }

        if include_promo_groups:
            data["allowed_promo_groups"] = [
                self._serialize_promo_group(promo, include_servers=False)
                for promo in squad.allowed_promo_groups
            ]

        return data

    # ------------------------------------------------------------------
    # Обработчики запросов
    # ------------------------------------------------------------------

    async def handle_health(self, request: web.Request) -> web.Response:
        return self._json_response(
            {
                "service": "bedolaga-bot",
                "admin_api": bool(settings.ADMIN_API_ENABLED),
                "version": settings.VERSION_CHECK_REPO,
            }
        )

    async def handle_overview(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        total_users = await session.scalar(select(func.count()).select_from(User))
        active_users = await session.scalar(
            select(func.count()).select_from(User).where(User.status == UserStatus.ACTIVE.value)
        )
        blocked_users = await session.scalar(
            select(func.count()).select_from(User).where(User.status == UserStatus.BLOCKED.value)
        )

        total_subscriptions = await session.scalar(
            select(func.count()).select_from(Subscription)
        )
        active_subscriptions = await session.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE.value
            )
        )
        expired_subscriptions = await session.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.status == SubscriptionStatus.EXPIRED.value
            )
        )

        total_balance = await session.scalar(select(func.coalesce(func.sum(User.balance_kopeks), 0)))
        total_revenue = await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
                Transaction.type.in_(
                    [
                        TransactionType.DEPOSIT.value,
                        TransactionType.SUBSCRIPTION_PAYMENT.value,
                    ]
                )
            )
        )

        open_tickets = await session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.OPEN.value)
        )
        pending_tickets = await session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.PENDING.value)
        )

        data = {
            "users": {
                "total": int(total_users or 0),
                "active": int(active_users or 0),
                "blocked": int(blocked_users or 0),
            },
            "subscriptions": {
                "total": int(total_subscriptions or 0),
                "active": int(active_subscriptions or 0),
                "expired": int(expired_subscriptions or 0),
            },
            "finance": {
                "total_balance_kopeks": int(total_balance or 0),
                "total_revenue_kopeks": int(total_revenue or 0),
            },
            "support": {
                "open": int(open_tickets or 0),
                "pending": int(pending_tickets or 0),
            },
        }

        return self._json_response(data)

    async def handle_users(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        params = request.rel_url.query
        page, page_size = self._pagination(params)
        search = (params.get("search") or "").strip()
        status_filter = (params.get("status") or "").strip()

        filters = []
        if status_filter:
            allowed_statuses = {status.value for status in UserStatus}
            if status_filter not in allowed_statuses:
                return self._json_error("invalid_status", status=400)
            filters.append(User.status == status_filter)

        if search:
            search_like = f"%{search.lower()}%"
            filters.append(
                or_(
                    func.lower(User.username).like(search_like),
                    func.lower(User.first_name).like(search_like),
                    func.lower(User.last_name).like(search_like),
                    cast(User.telegram_id, String).like(f"%{search}%"),
                    func.lower(User.referral_code).like(search_like),
                )
            )

        total_stmt = select(func.count()).select_from(User)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))
        total = await session.scalar(total_stmt)

        stmt = (
            select(User)
            .options(selectinload(User.promo_group), selectinload(User.subscription))
            .order_by(desc(User.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await session.execute(stmt)
        users = result.scalars().all()

        data = {
            "users": [self._serialize_user(user, include_subscription=True) for user in users]
        }
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_user_detail(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        user_id = int(request.match_info["user_id"])
        stmt = (
            select(User)
            .options(selectinload(User.promo_group), selectinload(User.subscription))
            .where(User.id == user_id)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            return self._json_error("user_not_found", status=404)

        return self._json_response({"user": self._serialize_user(user, include_subscription=True)})

    async def handle_user_subscription(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        user_id = int(request.match_info["user_id"])
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        result = await session.execute(stmt)
        subscription = result.scalar_one_or_none()

        if not subscription:
            return self._json_error("subscription_not_found", status=404)

        return self._json_response({"subscription": self._serialize_subscription(subscription)})

    async def handle_user_transactions(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        user_id = int(request.match_info["user_id"])
        params = request.rel_url.query
        page, page_size = self._pagination(params)

        total = await session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id)
        )

        stmt = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(desc(Transaction.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        transactions = result.scalars().all()

        data = {
            "transactions": [self._serialize_transaction(tx) for tx in transactions]
        }
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_subscriptions(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        params = request.rel_url.query
        page, page_size = self._pagination(params)
        status_filter = (params.get("status") or "").strip()
        trial_filter = params.get("is_trial")
        filters = []

        if status_filter:
            allowed_statuses = {status.value for status in SubscriptionStatus}
            if status_filter not in allowed_statuses:
                return self._json_error("invalid_status", status=400)
            filters.append(Subscription.status == status_filter)

        if trial_filter is not None:
            normalized = str(trial_filter).lower()
            if normalized in {"1", "true", "yes"}:
                filters.append(Subscription.is_trial.is_(True))
            elif normalized in {"0", "false", "no"}:
                filters.append(Subscription.is_trial.is_(False))

        total_stmt = select(func.count()).select_from(Subscription)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))
        total = await session.scalar(total_stmt)

        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.user))
            .order_by(desc(Subscription.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await session.execute(stmt)
        subscriptions = result.scalars().all()

        data = {
            "subscriptions": [self._serialize_subscription(sub) for sub in subscriptions]
        }
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_transactions(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        params = request.rel_url.query
        page, page_size = self._pagination(params)
        type_filter = (params.get("type") or "").strip()
        filters = []

        if type_filter:
            allowed_types = {tx_type.value for tx_type in TransactionType}
            if type_filter not in allowed_types:
                return self._json_error("invalid_type", status=400)
            filters.append(Transaction.type == type_filter)

        total_stmt = select(func.count()).select_from(Transaction)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))
        total = await session.scalar(total_stmt)

        stmt = (
            select(Transaction)
            .order_by(desc(Transaction.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await session.execute(stmt)
        transactions = result.scalars().all()

        data = {
            "transactions": [self._serialize_transaction(tx) for tx in transactions]
        }
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_payments(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        provider = request.match_info["provider"].lower()
        params = request.rel_url.query
        page, page_size = self._pagination(params)
        status_filter = (params.get("status") or "").strip()

        model_map = {
            "yookassa": YooKassaPayment,
            "cryptobot": CryptoBotPayment,
            "mulenpay": MulenPayPayment,
            "pal24": Pal24Payment,
        }

        model = model_map.get(provider)
        if model is None:
            return self._json_error("unknown_provider", status=404)

        filters = []
        if status_filter:
            filters.append(model.status == status_filter)

        total_stmt = select(func.count()).select_from(model)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))
        total = await session.scalar(total_stmt)

        stmt = (
            select(model)
            .order_by(desc(model.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await session.execute(stmt)
        payments = result.scalars().all()

        data = {"payments": [self._serialize_payment(provider, payment) for payment in payments]}
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_tickets(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        params = request.rel_url.query
        page, page_size = self._pagination(params)
        status_filter = (params.get("status") or "").strip()
        priority_filter = (params.get("priority") or "").strip()
        search = (params.get("search") or "").strip()

        filters = []
        if status_filter:
            allowed_statuses = {status.value for status in TicketStatus}
            if status_filter not in allowed_statuses:
                return self._json_error("invalid_status", status=400)
            filters.append(Ticket.status == status_filter)

        if priority_filter:
            filters.append(Ticket.priority == priority_filter)

        if search:
            search_like = f"%{search.lower()}%"
            filters.append(
                or_(
                    func.lower(Ticket.title).like(search_like),
                    cast(Ticket.id, String).like(f"%{search}%"),
                )
            )

        total_stmt = select(func.count()).select_from(Ticket)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))
        total = await session.scalar(total_stmt)

        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.user))
            .order_by(desc(Ticket.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await session.execute(stmt)
        tickets = result.scalars().unique().all()

        data = {"tickets": [self._serialize_ticket(ticket) for ticket in tickets]}
        meta = {"page": page, "page_size": page_size, "total": int(total or 0)}
        return self._json_response(data, meta=meta)

    async def handle_ticket_detail(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        ticket_id = int(request.match_info["ticket_id"])
        stmt = (
            select(Ticket)
            .options(
                selectinload(Ticket.user),
                selectinload(Ticket.messages).selectinload(TicketMessage.user),
            )
            .where(Ticket.id == ticket_id)
        )
        result = await session.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return self._json_error("ticket_not_found", status=404)

        return self._json_response({"ticket": self._serialize_ticket(ticket, include_messages=True)})

    async def handle_promo_groups(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        stmt = select(PromoGroup).options(selectinload(PromoGroup.server_squads)).order_by(
            PromoGroup.id
        )
        result = await session.execute(stmt)
        promo_groups = result.scalars().all()

        data = {
            "promo_groups": [self._serialize_promo_group(promo_group) for promo_group in promo_groups]
        }
        return self._json_response(data)

    async def handle_server_squads(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        stmt = select(ServerSquad).options(selectinload(ServerSquad.allowed_promo_groups)).order_by(
            ServerSquad.sort_order, ServerSquad.display_name
        )
        result = await session.execute(stmt)
        squads = result.scalars().all()

        data = {
            "server_squads": [
                self._serialize_server_squad(squad, include_promo_groups=True) for squad in squads
            ]
        }
        return self._json_response(data)

    async def handle_config_categories(self, request: web.Request) -> web.Response:
        categories = bot_configuration_service.get_categories()
        data = {
            "categories": [
                {
                    "key": key,
                    "label": label,
                    "items": count,
                }
                for key, label, count in categories
            ]
        }
        return self._json_response(data)

    def _serialize_setting(self, key: str) -> Dict[str, Any]:
        definition = bot_configuration_service.get_definition(key)
        summary = bot_configuration_service.get_setting_summary(key)
        current_value = bot_configuration_service.get_current_value(key)
        original_value = bot_configuration_service.get_original_value(key)
        choices = bot_configuration_service.get_choice_options(key)

        return {
            "key": key,
            "name": definition.display_name,
            "type": definition.type_label,
            "category_key": summary["category_key"],
            "category_label": summary["category_label"],
            "has_override": summary["has_override"],
            "is_optional": definition.is_optional,
            "current": self._jsonable(current_value),
            "original": self._jsonable(original_value),
            "choices": [
                {
                    "value": self._jsonable(option.value),
                    "label": option.label,
                    "description": option.description,
                }
                for option in choices
            ],
        }

    async def handle_config_settings(self, request: web.Request) -> web.Response:
        category = (request.rel_url.query.get("category") or "").strip()

        if category:
            definitions = bot_configuration_service.get_settings_for_category(category)
        else:
            bot_configuration_service.initialize_definitions()
            definitions = list(bot_configuration_service._definitions.values())  # type: ignore[attr-defined]

        keys = [definition.key for definition in definitions]
        data = {"settings": [self._serialize_setting(key) for key in keys]}
        return self._json_response(data)

    async def handle_config_setting_detail(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return self._json_error("setting_not_found", status=404)

        return self._json_response({"setting": self._serialize_setting(key)})

    def _convert_setting_value(self, key: str, value: Any) -> Any:
        definition = bot_configuration_service.get_definition(key)

        if value is None:
            if definition.is_optional:
                return None
            raise ValueError("value_required")

        python_type = definition.python_type

        if python_type is bool:
            if isinstance(value, bool):
                converted = value
            elif isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on", "да"}:
                    converted = True
                elif lowered in {"0", "false", "no", "off", "нет"}:
                    converted = False
                else:
                    raise ValueError("invalid_boolean")
            else:
                raise ValueError("invalid_boolean")
        elif python_type is int:
            try:
                converted = int(value)
            except (TypeError, ValueError):
                raise ValueError("invalid_integer") from None
        elif python_type is float:
            try:
                converted = float(value)
            except (TypeError, ValueError):
                raise ValueError("invalid_float") from None
        else:
            converted = str(value)

        choices = bot_configuration_service.get_choice_options(key)
        if choices:
            allowed_values = {option.value for option in choices}
            if converted not in allowed_values:
                raise ValueError("invalid_choice")

        return converted

    async def handle_config_setting_update(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return self._json_error("setting_not_found", status=404)

        try:
            payload = await request.json()
        except Exception:
            return self._json_error("invalid_json", status=400)

        if "value" not in payload:
            return self._json_error("value_required", status=400)

        try:
            converted_value = self._convert_setting_value(key, payload["value"])
        except ValueError as error:
            return self._json_error(str(error), status=400)

        await bot_configuration_service.set_value(session, key, converted_value)
        return self._json_response({"setting": self._serialize_setting(key)})

    async def handle_config_setting_reset(self, request: web.Request) -> web.Response:
        session = request.get("db")
        if session is None:
            return self._json_error("db_session_unavailable", status=500)

        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return self._json_error("setting_not_found", status=404)

        await bot_configuration_service.reset_value(session, key)
        return self._json_response({"setting": self._serialize_setting(key)})
