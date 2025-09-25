import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.admin_api_client import (
    SUPPORTED_AUTH_TYPES,
    count_audit_logs,
    create_admin_api_client,
    create_audit_log,
    delete_admin_api_client,
    get_active_client_by_cookie,
    get_active_client_by_token,
    get_admin_api_client_by_id,
    list_admin_api_clients,
    list_audit_logs,
    record_client_usage,
    update_admin_api_client,
)
from app.database.crud.ticket import TicketCRUD, TicketMessageCRUD
from app.database.crud.transaction import (
    get_user_total_spent_kopeks,
    get_user_transactions,
    get_user_transactions_count,
)
from app.database.crud.user import (
    add_user_balance,
    get_user_by_id,
    update_user,
    withdraw_user_balance,
)
from app.database.database import AsyncSessionLocal
from app.database.models import (
    AdminApiClient,
    PromoGroup,
    Subscription,
    SubscriptionStatus,
    Ticket,
    TicketStatus,
    Transaction,
    TransactionType,
    User,
)
from app.services.system_settings_service import bot_configuration_service
from app.utils.pagination import get_pagination_info


logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
PUBLIC_PATHS = {f"{API_PREFIX}/health"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _rubles(amount_kopeks: Optional[int]) -> float:
    if amount_kopeks is None:
        return 0.0
    return round(amount_kopeks / 100, 2)


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _hash_prefix(secret: str) -> str:
    import hashlib

    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(slots=True)
class AuthContext:
    client: Optional[AdminApiClient]
    auth_type: str
    token_prefix: Optional[str]
    is_bootstrap: bool = False


class AdminApiAuthError(web.HTTPUnauthorized):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(
            text=json.dumps({"detail": message}),
            content_type="application/json",
            status=status_code,
        )
        self.message = message


@dataclass(slots=True)
class _AuthCandidate:
    auth_type: str
    token: str
    basic_username: Optional[str] = None
    cookie_key: Optional[str] = None


class AdminApiAuthManager:
    def __init__(self) -> None:
        self._bootstrap = settings.get_web_api_bootstrap_credentials()

    @staticmethod
    def _get_ip(request: web.Request) -> Optional[str]:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.remote

    @staticmethod
    def _get_origin(request: web.Request) -> Optional[str]:
        origin = request.headers.get("Origin")
        if origin:
            return origin
        referer = request.headers.get("Referer")
        if referer and "//" in referer:
            return referer.split("//", 1)[1].split("/", 1)[0]
        return None

    def _extract_credentials(self, request: web.Request) -> List[_AuthCandidate]:
        candidates: List[_AuthCandidate] = []

        x_api_key = request.headers.get("X-Api-Key")
        if x_api_key:
            value = x_api_key.strip()
            if value.lower().startswith("basic "):
                decoded = self._decode_basic(value[6:].strip())
                if decoded:
                    candidates.append(decoded)
            else:
                candidates.append(_AuthCandidate(auth_type="api_key", token=value))

        authorization = request.headers.get("Authorization")
        if authorization:
            header = authorization.strip()
            if header.lower().startswith("bearer "):
                candidates.append(
                    _AuthCandidate(auth_type="bearer", token=header[7:].strip())
                )
            elif header.lower().startswith("basic "):
                decoded = self._decode_basic(header[6:].strip())
                if decoded:
                    candidates.append(decoded)

        for cookie_key, cookie_value in request.cookies.items():
            candidates.append(
                _AuthCandidate(
                    auth_type="cookie",
                    token=cookie_value,
                    cookie_key=cookie_key,
                )
            )

        return candidates

    @staticmethod
    def _decode_basic(encoded: str) -> Optional[_AuthCandidate]:
        import base64

        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            return None

        if ":" not in decoded:
            return None
        username, password = decoded.split(":", 1)
        return _AuthCandidate(
            auth_type="basic",
            token=f"{username}:{password}",
            basic_username=username,
        )

    async def authenticate(self, request: web.Request, session: AsyncSessionLocal) -> AuthContext:
        candidates = self._extract_credentials(request)
        if not candidates:
            raise AdminApiAuthError("Не указаны учетные данные", status_code=401)

        selected_client: Optional[AdminApiClient] = None
        selected_candidate: Optional[_AuthCandidate] = None

        for candidate in candidates:
            try:
                if candidate.auth_type == "cookie" and candidate.cookie_key:
                    selected_client = await get_active_client_by_cookie(
                        session,
                        cookie_key=candidate.cookie_key,
                        cookie_value=candidate.token,
                    )
                else:
                    selected_client = await get_active_client_by_token(
                        session,
                        token=candidate.token,
                        auth_type=candidate.auth_type,
                        basic_username=candidate.basic_username,
                    )
            except Exception as error:  # noqa: BLE001
                logger.error("Ошибка проверки admin api токена: %s", error)
                selected_client = None

            if selected_client:
                if not self._is_origin_allowed(selected_client, self._get_origin(request)):
                    raise AdminApiAuthError("Доступ с данного Origin запрещен", status_code=403)
                if not self._is_ip_allowed(selected_client, self._get_ip(request)):
                    raise AdminApiAuthError("Доступ с данного IP запрещен", status_code=403)

                await record_client_usage(
                    session,
                    selected_client,
                    ip_address=self._get_ip(request),
                    user_agent=request.headers.get("User-Agent"),
                )
                selected_candidate = candidate
                break

        if selected_client and selected_candidate:
            return AuthContext(
                client=selected_client,
                auth_type=selected_candidate.auth_type,
                token_prefix=selected_client.token_prefix,
                is_bootstrap=False,
            )

        bootstrap = self._bootstrap
        if bootstrap:
            for candidate in candidates:
                if candidate.auth_type in {"api_key", "bearer"} and bootstrap.get("token"):
                    if candidate.token == bootstrap["token"]:
                        return AuthContext(
                            client=None,
                            auth_type=candidate.auth_type,
                            token_prefix=_hash_prefix(candidate.token),
                            is_bootstrap=True,
                        )
                if (
                    candidate.auth_type == "basic"
                    and bootstrap.get("basic_user")
                    and bootstrap.get("basic_password")
                ):
                    expected = f"{bootstrap['basic_user']}:{bootstrap['basic_password']}"
                    if candidate.token == expected:
                        return AuthContext(
                            client=None,
                            auth_type="basic",
                            token_prefix=_hash_prefix(candidate.token),
                            is_bootstrap=True,
                        )
                if (
                    candidate.auth_type == "cookie"
                    and bootstrap.get("cookie_key")
                    and bootstrap.get("cookie_value")
                ):
                    if (
                        candidate.cookie_key == bootstrap["cookie_key"]
                        and candidate.token == bootstrap["cookie_value"]
                    ):
                        return AuthContext(
                            client=None,
                            auth_type="cookie",
                            token_prefix=_hash_prefix(candidate.token),
                            is_bootstrap=True,
                        )

        raise AdminApiAuthError("Недействительные учетные данные", status_code=401)

    @staticmethod
    def _is_origin_allowed(client: AdminApiClient, origin: Optional[str]) -> bool:
        allowed = client.allowed_origins or []
        if not allowed or "*" in allowed:
            return True
        if not origin:
            return False
        return origin in allowed

    @staticmethod
    def _is_ip_allowed(client: AdminApiClient, ip_address: Optional[str]) -> bool:
        allowed = client.allowed_ips or []
        if not allowed or "*" in allowed:
            return True
        if not ip_address:
            return False
        return ip_address in allowed

    async def log_request(
        self,
        request: web.Request,
        response: Optional[web.StreamResponse],
        auth_context: Optional[AuthContext],
        started_at: float,
        *,
        error: Optional[Exception] = None,
    ) -> None:
        if request.path in PUBLIC_PATHS:
            return

        status_code = 500
        if isinstance(response, web.StreamResponse):
            status_code = response.status
        elif response is not None:
            status_code = getattr(response, "status", 200)

        duration_ms = (time.perf_counter() - started_at) * 1000
        metadata: Dict[str, Any] = {}
        if error:
            metadata["error"] = str(error)

        try:
            async with AsyncSessionLocal() as session:
                await create_audit_log(
                    session,
                    client=auth_context.client if auth_context else None,
                    token_prefix=auth_context.token_prefix if auth_context else None,
                    auth_type=auth_context.auth_type if auth_context else None,
                    method=request.method,
                    path=request.path,
                    status_code=status_code,
                    ip_address=self._get_ip(request),
                    user_agent=request.headers.get("User-Agent"),
                    response_time_ms=duration_ms,
                    metadata=metadata or None,
                )
        except Exception as log_error:  # noqa: BLE001
            logger.debug("Не удалось записать лог admin API: %s", log_error)


class AdminAPIServer:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._auth_manager = AdminApiAuthManager()
        self._allowed_origins = settings.get_web_api_allowed_origins()

    async def start(self) -> None:
        if not settings.is_web_api_enabled():
            logger.info("Административный API отключен настройками")
            return

        if self.app is None:
            self.app = web.Application(
                middlewares=[
                    self._error_middleware,
                    self._cors_middleware,
                    self._auth_middleware,
                ]
            )
            self._setup_routes()

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner,
            host=settings.WEB_API_HOST,
            port=settings.WEB_API_PORT,
        )
        await self.site.start()
        logger.info(
            "Административный API запущен на %s:%s",
            settings.WEB_API_HOST,
            settings.WEB_API_PORT,
        )

    async def stop(self) -> None:
        if self.site:
            await self.site.stop()
            self.site = None
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        if self.app:
            await self.app.shutdown()
            await self.app.cleanup()
            self.app = None

    # ---------------------- Middlewares ----------------------

    @web.middleware
    async def _error_middleware(self, request: web.Request, handler):
        try:
            response = await handler(request)
            return response
        except AdminApiAuthError as auth_error:
            return self._apply_cors(auth_error, request)
        except web.HTTPException as exc:
            if exc.status >= 400:
                payload = {"detail": exc.text or exc.reason or "HTTP error"}
                response = web.json_response(payload, status=exc.status)
                return self._apply_cors(response, request)
            raise
        except Exception as error:  # noqa: BLE001
            logger.exception("Ошибка обработки admin API запроса: %s", error)
            response = web.json_response(
                {"detail": "Внутренняя ошибка сервера"}, status=500
            )
            return self._apply_cors(response, request)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            response = web.Response(status=204)
            return self._apply_cors(response, request)

        response = await handler(request)
        if isinstance(response, web.StreamResponse):
            return self._apply_cors(response, request)
        return response

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await handler(request)

        start_time = time.perf_counter()
        auth_context: Optional[AuthContext] = None
        try:
            async with AsyncSessionLocal() as session:
                auth_context = await self._auth_manager.authenticate(request, session)
        except AdminApiAuthError as auth_error:
            return self._apply_cors(auth_error, request)

        try:
            response = await handler(request)
        except web.HTTPException as exc:
            await self._auth_manager.log_request(request, exc, auth_context, start_time)
            raise
        except Exception as error:  # noqa: BLE001
            await self._auth_manager.log_request(
                request,
                None,
                auth_context,
                start_time,
                error=error,
            )
            raise
        else:
            await self._auth_manager.log_request(request, response, auth_context, start_time)
            return response

    def _apply_cors(self, response: web.StreamResponse, request: web.Request) -> web.StreamResponse:
        origin = request.headers.get("Origin")
        allow_origin = "*"
        if self._allowed_origins and "*" not in self._allowed_origins:
            if origin and origin in self._allowed_origins:
                allow_origin = origin
            elif len(self._allowed_origins) == 1:
                allow_origin = self._allowed_origins[0]
            else:
                allow_origin = "null"
        elif origin:
            allow_origin = origin

        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Api-Key"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    # ---------------------- Routing ----------------------

    def _setup_routes(self) -> None:
        assert self.app is not None
        router = self.app.router

        router.add_get(f"{API_PREFIX}/health", self.handle_health)
        router.add_get(f"{API_PREFIX}/meta", self.handle_meta)
        router.add_get(f"{API_PREFIX}/dashboard/summary", self.handle_dashboard_summary)

        router.add_get(f"{API_PREFIX}/users", self.handle_users_list)
        router.add_get(f"{API_PREFIX}/users/{{user_id:int}}", self.handle_user_detail)
        router.add_patch(f"{API_PREFIX}/users/{{user_id:int}}", self.handle_user_update)
        router.add_post(f"{API_PREFIX}/users/{{user_id:int}}/balance", self.handle_user_balance_adjust)
        router.add_get(f"{API_PREFIX}/users/{{user_id:int}}/transactions", self.handle_user_transactions)

        router.add_get(f"{API_PREFIX}/subscriptions", self.handle_subscriptions_list)
        router.add_get(f"{API_PREFIX}/transactions", self.handle_transactions_list)

        router.add_get(f"{API_PREFIX}/tickets", self.handle_tickets_list)
        router.add_get(f"{API_PREFIX}/tickets/{{ticket_id:int}}", self.handle_ticket_detail)
        router.add_post(f"{API_PREFIX}/tickets/{{ticket_id:int}}/reply", self.handle_ticket_reply)
        router.add_patch(f"{API_PREFIX}/tickets/{{ticket_id:int}}", self.handle_ticket_update)

        router.add_get(f"{API_PREFIX}/system-settings", self.handle_settings_list)
        router.add_get(f"{API_PREFIX}/system-settings/{{key}}", self.handle_setting_detail)
        router.add_patch(f"{API_PREFIX}/system-settings/{{key}}", self.handle_setting_update)
        router.add_delete(f"{API_PREFIX}/system-settings/{{key}}", self.handle_setting_reset)

        router.add_get(f"{API_PREFIX}/admin-api/clients", self.handle_admin_clients_list)
        router.add_post(f"{API_PREFIX}/admin-api/clients", self.handle_admin_client_create)
        router.add_patch(f"{API_PREFIX}/admin-api/clients/{{client_id:int}}", self.handle_admin_client_update)
        router.add_delete(f"{API_PREFIX}/admin-api/clients/{{client_id:int}}", self.handle_admin_client_delete)

        router.add_get(f"{API_PREFIX}/admin-api/audit-logs", self.handle_admin_audit_logs)

    # ---------------------- Basic info handlers ----------------------

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "timestamp": _iso(_now_utc())})

    async def handle_meta(self, request: web.Request) -> web.Response:
        data = {
            "service": "Remnawave Bedolaga Admin API",
            "version": getattr(settings, "APP_VERSION", None) or "unknown",
            "web_api_host": settings.WEB_API_HOST,
            "web_api_port": settings.WEB_API_PORT,
            "allowed_origins": self._allowed_origins,
            "auth_methods": sorted(SUPPORTED_AUTH_TYPES),
        }
        return web.json_response(data)

    async def handle_dashboard_summary(self, request: web.Request) -> web.Response:
        now = _now_utc()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        async with AsyncSessionLocal() as session:
            total_users = await session.execute(select(func.count(User.id)))
            active_users = await session.execute(
                select(func.count(User.id)).where(User.status == "active")
            )
            blocked_users = await session.execute(
                select(func.count(User.id)).where(User.status == "blocked")
            )
            new_users = await session.execute(
                select(func.count(User.id)).where(User.created_at >= day_ago.replace(tzinfo=None))
            )
            balance_sum = await session.execute(
                select(func.coalesce(func.sum(User.balance_kopeks), 0))
            )

            active_subscriptions = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.end_date > now.replace(tzinfo=None),
                )
            )

            trial_subscriptions = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.is_trial.is_(True),
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                )
            )

            revenue_week = await session.execute(
                select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
                    Transaction.type.in_(
                        [
                            TransactionType.DEPOSIT.value,
                            TransactionType.SUBSCRIPTION_PAYMENT.value,
                        ]
                    ),
                    Transaction.created_at >= week_ago.replace(tzinfo=None),
                )
            )

        payload = {
            "users": {
                "total": int(total_users.scalar() or 0),
                "active": int(active_users.scalar() or 0),
                "blocked": int(blocked_users.scalar() or 0),
                "new_last_24h": int(new_users.scalar() or 0),
            },
            "subscriptions": {
                "active": int(active_subscriptions.scalar() or 0),
                "trial": int(trial_subscriptions.scalar() or 0),
            },
            "balance": {
                "total_kopeks": int(balance_sum.scalar() or 0),
                "total_rubles": _rubles(balance_sum.scalar()),
            },
            "revenue": {
                "week_kopeks": int(revenue_week.scalar() or 0),
                "week_rubles": _rubles(revenue_week.scalar()),
            },
        }
        return web.json_response(payload)

    # ---------------------- Subscriptions ----------------------

    async def handle_subscriptions_list(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(params.get("page_size", settings.get_web_api_default_page_size()))
        except ValueError:
            page_size = settings.get_web_api_default_page_size()
        page_size = min(max(page_size, 1), settings.get_web_api_max_page_size())
        offset = (page - 1) * page_size

        status_filter = params.get("status")
        user_search = params.get("user_search")
        is_trial = _parse_bool(params.get("is_trial"))
        expires_before = params.get("expires_before")
        expires_after = params.get("expires_after")

        conditions = []

        if status_filter:
            if status_filter not in {item.value for item in SubscriptionStatus}:
                return web.json_response({"detail": "Недопустимый статус подписки"}, status=400)
            conditions.append(Subscription.status == status_filter)

        if is_trial is not None:
            conditions.append(Subscription.is_trial.is_(True if is_trial else False))

        if expires_before:
            try:
                expires_dt = datetime.fromisoformat(expires_before)
                conditions.append(Subscription.end_date <= expires_dt.replace(tzinfo=None))
            except ValueError:
                return web.json_response({"detail": "Некорректный expires_before"}, status=400)

        if expires_after:
            try:
                expires_dt = datetime.fromisoformat(expires_after)
                conditions.append(Subscription.end_date >= expires_dt.replace(tzinfo=None))
            except ValueError:
                return web.json_response({"detail": "Некорректный expires_after"}, status=400)

        if user_search:
            pattern = f"%{user_search.lower()}%"
            user_conditions = [
                func.lower(User.username).like(pattern),
                func.lower(User.first_name).like(pattern),
                func.lower(User.last_name).like(pattern),
            ]
            if user_search.isdigit():
                try:
                    user_conditions.append(User.telegram_id == int(user_search))
                except ValueError:
                    pass
            conditions.append(Subscription.user.has(or_(*user_conditions)))

        async with AsyncSessionLocal() as session:
            query = (
                select(Subscription)
                .options(selectinload(Subscription.user))
                .order_by(Subscription.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            count_query = select(func.count(Subscription.id))

            if conditions:
                query = query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))

            result = await session.execute(query)
            subscriptions = result.scalars().all()

            total_result = await session.execute(count_query)
            total_count = int(total_result.scalar() or 0)

        payload = {
            "items": [self._serialize_subscription(sub) for sub in subscriptions],
            "pagination": get_pagination_info(total_count, page=page, per_page=page_size),
        }
        return web.json_response(payload)

    # ---------------------- Transactions ----------------------

    async def handle_transactions_list(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(params.get("page_size", settings.get_web_api_default_page_size()))
        except ValueError:
            page_size = settings.get_web_api_default_page_size()
        page_size = min(max(page_size, 1), settings.get_web_api_max_page_size())
        offset = (page - 1) * page_size

        type_filter = params.get("type")
        payment_method = params.get("payment_method")
        user_id = params.get("user_id")
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        min_amount = params.get("min_amount")
        max_amount = params.get("max_amount")
        is_completed = _parse_bool(params.get("is_completed"))

        conditions = []

        if type_filter:
            allowed_types = {item.value for item in TransactionType}
            if type_filter not in allowed_types:
                return web.json_response({"detail": "Недопустимый тип транзакции"}, status=400)
            conditions.append(Transaction.type == type_filter)

        if payment_method:
            conditions.append(Transaction.payment_method == payment_method)

        if user_id:
            try:
                conditions.append(Transaction.user_id == int(user_id))
            except ValueError:
                return web.json_response({"detail": "user_id должен быть числом"}, status=400)

        if date_from:
            try:
                date_value = datetime.fromisoformat(date_from)
                conditions.append(Transaction.created_at >= date_value.replace(tzinfo=None))
            except ValueError:
                return web.json_response({"detail": "Некорректный date_from"}, status=400)

        if date_to:
            try:
                date_value = datetime.fromisoformat(date_to)
                conditions.append(Transaction.created_at <= date_value.replace(tzinfo=None))
            except ValueError:
                return web.json_response({"detail": "Некорректный date_to"}, status=400)

        if min_amount:
            try:
                conditions.append(Transaction.amount_kopeks >= int(float(min_amount) * 100))
            except ValueError:
                return web.json_response({"detail": "Некорректный min_amount"}, status=400)

        if max_amount:
            try:
                conditions.append(Transaction.amount_kopeks <= int(float(max_amount) * 100))
            except ValueError:
                return web.json_response({"detail": "Некорректный max_amount"}, status=400)

        if is_completed is not None:
            conditions.append(Transaction.is_completed.is_(True if is_completed else False))

        async with AsyncSessionLocal() as session:
            query = (
                select(Transaction)
                .options(selectinload(Transaction.user))
                .order_by(Transaction.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            count_query = select(func.count(Transaction.id))

            if conditions:
                query = query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))

            result = await session.execute(query)
            transactions = result.scalars().all()

            total_result = await session.execute(count_query)
            total_count = int(total_result.scalar() or 0)

        payload = {
            "items": [self._serialize_transaction(tx) for tx in transactions],
            "pagination": get_pagination_info(total_count, page=page, per_page=page_size),
        }
        return web.json_response(payload)

    # ---------------------- Tickets ----------------------

    async def handle_tickets_list(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(params.get("page_size", settings.get_web_api_default_page_size()))
        except ValueError:
            page_size = settings.get_web_api_default_page_size()
        page_size = min(max(page_size, 1), settings.get_web_api_max_page_size())
        offset = (page - 1) * page_size

        status_filter = params.get("status")
        priority_filter = params.get("priority")

        async with AsyncSessionLocal() as session:
            tickets = await TicketCRUD.get_all_tickets(
                session,
                status=status_filter,
                priority=priority_filter,
                limit=page_size,
                offset=offset,
            )
            total = await TicketCRUD.count_tickets(session, status=status_filter)

        payload = {
            "items": [self._serialize_ticket(ticket) for ticket in tickets],
            "pagination": get_pagination_info(total, page=page, per_page=page_size),
        }
        return web.json_response(payload)

    async def handle_ticket_detail(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        async with AsyncSessionLocal() as session:
            ticket = await TicketCRUD.get_ticket_by_id(session, ticket_id, load_messages=True, load_user=True)
            if not ticket:
                return web.json_response({"detail": "Тикет не найден"}, status=404)

        return web.json_response(self._serialize_ticket(ticket, include_messages=True))

    async def handle_ticket_reply(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        message_text = (payload.get("message") or "").strip()
        if not message_text:
            return web.json_response({"detail": "Текст сообщения обязателен"}, status=400)

        is_from_admin = payload.get("is_from_admin", True)
        async with AsyncSessionLocal() as session:
            ticket = await TicketCRUD.get_ticket_by_id(session, ticket_id, load_messages=False)
            if not ticket:
                return web.json_response({"detail": "Тикет не найден"}, status=404)

            message = await TicketMessageCRUD.add_message(
                session,
                ticket_id=ticket.id,
                user_id=ticket.user_id,
                message_text=message_text,
                is_from_admin=bool(is_from_admin),
            )

        return web.json_response(self._serialize_ticket_message(message))

    async def handle_ticket_update(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        async with AsyncSessionLocal() as session:
            ticket = await TicketCRUD.get_ticket_by_id(session, ticket_id, load_messages=False)
            if not ticket:
                return web.json_response({"detail": "Тикет не найден"}, status=404)

            updates_applied = False

            if "status" in payload:
                new_status = payload["status"]
                allowed_statuses = {item.value for item in TicketStatus}
                if new_status not in allowed_statuses:
                    return web.json_response({"detail": "Недопустимый статус тикета"}, status=400)
                await TicketCRUD.update_ticket_status(session, ticket.id, new_status)
                updates_applied = True

            if "priority" in payload:
                ticket.priority = str(payload["priority"])
                updates_applied = True

            if payload.get("block_user_permanent"):
                await TicketCRUD.set_user_reply_block(
                    session,
                    ticket.id,
                    permanent=True,
                    until=None,
                )
                updates_applied = True
            elif "block_user_until" in payload:
                until_value = payload["block_user_until"]
                if until_value:
                    try:
                        until_dt = datetime.fromisoformat(until_value)
                    except ValueError:
                        return web.json_response({"detail": "Некорректная дата block_user_until"}, status=400)
                    await TicketCRUD.set_user_reply_block(
                        session,
                        ticket.id,
                        permanent=False,
                        until=until_dt.replace(tzinfo=None),
                    )
                else:
                    await TicketCRUD.set_user_reply_block(session, ticket.id, permanent=False, until=None)
                updates_applied = True

            if payload.get("unblock_user"):
                await TicketCRUD.set_user_reply_block(session, ticket.id, permanent=False, until=None)
                updates_applied = True

            if updates_applied:
                await session.refresh(ticket)

        return web.json_response(self._serialize_ticket(ticket))

    # ---------------------- System settings ----------------------

    async def handle_settings_list(self, request: web.Request) -> web.Response:
        category = request.rel_url.query.get("category")
        definitions = self._collect_definitions(category)
        payload = [self._serialize_setting(defn.key) for defn in definitions]
        return web.json_response(payload)

    async def handle_setting_detail(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response({"detail": "Настройка не найдена"}, status=404)
        return web.json_response(self._serialize_setting(key))

    async def handle_setting_update(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        try:
            definition = bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response({"detail": "Настройка не найдена"}, status=404)

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        if "value" not in payload:
            return web.json_response({"detail": "Поле value обязательно"}, status=400)

        try:
            value = self._coerce_setting_value(definition.key, payload["value"])
        except ValueError as error:
            return web.json_response({"detail": str(error)}, status=400)

        async with AsyncSessionLocal() as session:
            await bot_configuration_service.set_value(session, key, value)
            await session.commit()

        return web.json_response(self._serialize_setting(key))

    async def handle_setting_reset(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            return web.json_response({"detail": "Настройка не найдена"}, status=404)

        async with AsyncSessionLocal() as session:
            await bot_configuration_service.reset_value(session, key)
            await session.commit()

        return web.json_response(self._serialize_setting(key))

    # ---------------------- Admin API clients ----------------------

    async def handle_admin_clients_list(self, request: web.Request) -> web.Response:
        include_inactive = _parse_bool(request.rel_url.query.get("include_inactive"))
        async with AsyncSessionLocal() as session:
            clients = await list_admin_api_clients(session, include_inactive=bool(include_inactive))
        return web.json_response([self._serialize_api_client(client) for client in clients])

    async def handle_admin_client_create(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        name = (payload.get("name") or "").strip()
        if not name:
            return web.json_response({"detail": "Поле name обязательно"}, status=400)

        auth_type = (payload.get("auth_type") or "api_key").lower()
        if auth_type not in SUPPORTED_AUTH_TYPES:
            return web.json_response({"detail": "Недопустимый auth_type"}, status=400)

        allowed_origins = self._to_string_list(payload.get("allowed_origins"))
        allowed_ips = self._to_string_list(payload.get("allowed_ips"))
        permissions = self._to_string_list(payload.get("permissions"))
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return web.json_response({"detail": "metadata должен быть объектом"}, status=400)

        secret_value = payload.get("token")
        basic_username = payload.get("basic_username")
        basic_password = payload.get("basic_password")
        cookie_key = payload.get("cookie_key")
        cookie_value = payload.get("cookie_value") or payload.get("cookie_secret")

        if auth_type == "basic" and not (basic_username and basic_password):
            return web.json_response({"detail": "Для basic необходимо basic_username и basic_password"}, status=400)

        if auth_type == "cookie" and not (cookie_key and cookie_value):
            return web.json_response({"detail": "Для cookie необходимо cookie_key и cookie_value"}, status=400)

        async with AsyncSessionLocal() as session:
            created = await create_admin_api_client(
                session,
                name=name,
                description=payload.get("description"),
                auth_type=auth_type,
                secret=basic_password if auth_type == "basic" else (cookie_value if auth_type == "cookie" else secret_value),
                basic_username=basic_username,
                cookie_key=cookie_key,
                allowed_origins=allowed_origins,
                allowed_ips=allowed_ips,
                permissions=permissions,
                metadata=metadata,
            )

        response_payload = {
            "client": self._serialize_api_client(created.client, include_sensitive=True),
        }
        if created.token:
            response_payload["token"] = created.token
        if created.basic_password:
            response_payload["basic_password"] = created.basic_password
        if created.cookie_value:
            response_payload["cookie_value"] = created.cookie_value
        return web.json_response(response_payload, status=201)

    async def handle_admin_client_update(self, request: web.Request) -> web.Response:
        client_id = int(request.match_info["client_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        async with AsyncSessionLocal() as session:
            client = await get_admin_api_client_by_id(session, client_id)
            if not client:
                return web.json_response({"detail": "Клиент не найден"}, status=404)

            metadata = payload.get("metadata")
            if metadata is not None and not isinstance(metadata, dict):
                return web.json_response({"detail": "metadata должен быть объектом"}, status=400)

            updated = await update_admin_api_client(
                session,
                client,
                name=payload.get("name"),
                description=payload.get("description"),
                is_active=payload.get("is_active"),
                allowed_origins=self._to_string_list(payload.get("allowed_origins")),
                allowed_ips=self._to_string_list(payload.get("allowed_ips")),
                permissions=self._to_string_list(payload.get("permissions")),
                metadata=metadata,
            )

        return web.json_response(self._serialize_api_client(updated, include_sensitive=True))

    async def handle_admin_client_delete(self, request: web.Request) -> web.Response:
        client_id = int(request.match_info["client_id"])
        async with AsyncSessionLocal() as session:
            client = await get_admin_api_client_by_id(session, client_id)
            if not client:
                return web.json_response({"detail": "Клиент не найден"}, status=404)
            await delete_admin_api_client(session, client)
        return web.json_response({"status": "deleted"})

    async def handle_admin_audit_logs(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(params.get("page_size", settings.get_web_api_default_page_size()))
        except ValueError:
            page_size = settings.get_web_api_default_page_size()
        page_size = min(max(page_size, 1), settings.get_web_api_max_page_size())
        offset = (page - 1) * page_size

        client_id = params.get("client_id")
        token_prefix = params.get("token_prefix")
        status_code = params.get("status_code")
        auth_type = params.get("auth_type")

        if status_code is not None:
            try:
                status_code_int = int(status_code)
            except ValueError:
                return web.json_response({"detail": "status_code должен быть числом"}, status=400)
        else:
            status_code_int = None

        if client_id is not None:
            try:
                client_id_int = int(client_id)
            except ValueError:
                return web.json_response({"detail": "client_id должен быть числом"}, status=400)
        else:
            client_id_int = None

        async with AsyncSessionLocal() as session:
            logs = await list_audit_logs(
                session,
                limit=page_size,
                offset=offset,
                client_id=client_id_int,
                token_prefix=token_prefix,
                status_code=status_code_int,
                auth_type=auth_type,
            )
            total = await count_audit_logs(
                session,
                client_id=client_id_int,
                token_prefix=token_prefix,
                status_code=status_code_int,
                auth_type=auth_type,
            )

        payload = {
            "items": [self._serialize_audit_log(log) for log in logs],
            "pagination": get_pagination_info(total, page=page, per_page=page_size),
        }
        return web.json_response(payload)

    # ---------------------- Helpers ----------------------

    def _serialize_user(self, user: User) -> Dict[str, Any]:
        data = {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "status": user.status,
            "language": user.language,
            "balance": {
                "kopeks": user.balance_kopeks,
                "rubles": _rubles(user.balance_kopeks),
            },
            "created_at": _iso(user.created_at),
            "updated_at": _iso(user.updated_at),
            "last_activity": _iso(user.last_activity),
            "remnawave_uuid": user.remnawave_uuid,
            "has_had_paid_subscription": bool(user.has_had_paid_subscription),
            "has_made_first_topup": bool(user.has_made_first_topup),
        }
        if user.promo_group:
            data["promo_group"] = {
                "id": user.promo_group.id,
                "name": user.promo_group.name,
            }
        if user.subscription:
            data["subscription"] = self._serialize_subscription(user.subscription)
        return data

    def _serialize_subscription(self, subscription: Subscription) -> Dict[str, Any]:
        return {
            "id": subscription.id,
            "user_id": subscription.user_id,
            "status": subscription.status,
            "is_trial": subscription.is_trial,
            "start_date": _iso(subscription.start_date),
            "end_date": _iso(subscription.end_date),
            "traffic_limit_gb": subscription.traffic_limit_gb,
            "traffic_used_gb": subscription.traffic_used_gb,
            "device_limit": subscription.device_limit,
            "subscription_url": subscription.subscription_url,
            "subscription_crypto_link": subscription.subscription_crypto_link,
            "remnawave_short_uuid": subscription.remnawave_short_uuid,
            "autopay_enabled": subscription.autopay_enabled,
            "autopay_days_before": subscription.autopay_days_before,
            "created_at": _iso(subscription.created_at),
            "updated_at": _iso(subscription.updated_at),
        }

    def _serialize_transaction(self, tx: Transaction) -> Dict[str, Any]:
        return {
            "id": tx.id,
            "user_id": tx.user_id,
            "type": tx.type,
            "amount_kopeks": tx.amount_kopeks,
            "amount_rubles": _rubles(tx.amount_kopeks),
            "description": tx.description,
            "payment_method": tx.payment_method,
            "external_id": tx.external_id,
            "is_completed": tx.is_completed,
            "created_at": _iso(tx.created_at),
            "completed_at": _iso(tx.completed_at),
            "user": {
                "id": tx.user.id,
                "telegram_id": tx.user.telegram_id,
                "username": tx.user.username,
            } if tx.user else None,
        }

    def _serialize_ticket(self, ticket: Ticket, *, include_messages: bool = False) -> Dict[str, Any]:
        data = {
            "id": ticket.id,
            "user_id": ticket.user_id,
            "title": ticket.title,
            "status": ticket.status,
            "priority": ticket.priority,
            "user_reply_block_permanent": ticket.user_reply_block_permanent,
            "user_reply_block_until": _iso(ticket.user_reply_block_until),
            "created_at": _iso(ticket.created_at),
            "updated_at": _iso(ticket.updated_at),
            "closed_at": _iso(ticket.closed_at),
        }
        if ticket.user:
            data["user"] = {
                "id": ticket.user.id,
                "telegram_id": ticket.user.telegram_id,
                "username": ticket.user.username,
            }
        if include_messages:
            data["messages"] = [self._serialize_ticket_message(msg) for msg in ticket.messages]
        return data

    def _serialize_ticket_message(self, message) -> Dict[str, Any]:
        return {
            "id": message.id,
            "ticket_id": message.ticket_id,
            "user_id": message.user_id,
            "message_text": message.message_text,
            "is_from_admin": message.is_from_admin,
            "created_at": _iso(message.created_at),
        }

    def _serialize_setting(self, key: str) -> Dict[str, Any]:
        definition = bot_configuration_service.get_definition(key)
        current = bot_configuration_service.get_current_value(key)
        original = bot_configuration_service.get_original_value(key)
        choices = bot_configuration_service.get_choice_options(key)
        return {
            "key": key,
            "name": definition.display_name,
            "category_key": definition.category_key,
            "category_label": definition.category_label,
            "type": definition.type_label,
            "is_optional": definition.is_optional,
            "current_value": current,
            "original_value": original,
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

    def _serialize_api_client(self, client: AdminApiClient, *, include_sensitive: bool = False) -> Dict[str, Any]:
        data = {
            "id": client.id,
            "name": client.name,
            "description": client.description,
            "auth_type": client.auth_type,
            "token_prefix": client.token_prefix,
            "basic_username": client.basic_username,
            "cookie_key": client.cookie_key,
            "allowed_origins": client.allowed_origins or [],
            "allowed_ips": client.allowed_ips or [],
            "permissions": client.permissions or [],
            "is_active": client.is_active,
            "last_used_at": _iso(client.last_used_at),
            "last_used_ip": client.last_used_ip,
            "last_user_agent": client.last_user_agent,
            "created_at": _iso(client.created_at),
            "updated_at": _iso(client.updated_at),
        }
        if include_sensitive:
            data["metadata"] = client.metadata_json
        return data

    def _serialize_audit_log(self, log) -> Dict[str, Any]:
        return {
            "id": log.id,
            "client_id": log.client_id,
            "token_prefix": log.token_prefix,
            "auth_type": log.auth_type,
            "method": log.method,
            "path": log.path,
            "status_code": log.status_code,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "response_time_ms": log.response_time_ms,
            "metadata": log.metadata_json,
            "created_at": _iso(log.created_at),
        }

    def _collect_definitions(self, category: Optional[str]):
        if category:
            try:
                return bot_configuration_service.get_settings_for_category(category)
            except KeyError:
                return []

        definitions = []
        for category_key, _, _ in bot_configuration_service.get_categories():
            definitions.extend(bot_configuration_service.get_settings_for_category(category_key))
        return definitions

    @staticmethod
    def _to_string_list(value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value]
            return [item for item in items if item]
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return parts or None
        text = str(value).strip()
        return [text] if text else None

    def _coerce_setting_value(self, key: str, value: Any) -> Any:
        definition = bot_configuration_service.get_definition(key)
        python_type = definition.python_type

        if value is None:
            if not definition.is_optional:
                raise ValueError("Значение не может быть null")
            return None

        if python_type is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on", "да"}:
                    return True
                if lowered in {"false", "0", "no", "off", "нет"}:
                    return False
            raise ValueError("Некорректное булево значение")

        if python_type is int:
            try:
                return int(value)
            except (TypeError, ValueError) as error:
                raise ValueError("Некорректное целочисленное значение") from error

        if python_type is float:
            try:
                return float(value)
            except (TypeError, ValueError) as error:
                raise ValueError("Некорректное числовое значение") from error

        return value

    # ---------------------- Users ----------------------

    async def handle_users_list(self, request: web.Request) -> web.Response:
        params = request.rel_url.query

        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1

        try:
            page_size = int(params.get("page_size", settings.get_web_api_default_page_size()))
        except ValueError:
            page_size = settings.get_web_api_default_page_size()
        page_size = min(max(page_size, 1), settings.get_web_api_max_page_size())

        search = params.get("search")
        status_filter = params.get("status")
        promo_group_id = params.get("promo_group_id")
        has_subscription = _parse_bool(params.get("has_subscription"))
        is_trial = _parse_bool(params.get("is_trial"))
        sort_param = params.get("sort", "-created_at")

        conditions = []

        if search:
            pattern = f"%{search.lower()}%"
            search_conditions = [
                func.lower(User.username).like(pattern),
                func.lower(User.first_name).like(pattern),
                func.lower(User.last_name).like(pattern),
            ]
            if search.isdigit():
                try:
                    search_conditions.append(User.telegram_id == int(search))
                except ValueError:
                    pass
            conditions.append(or_(*search_conditions))

        if status_filter:
            allowed_statuses = {"active", "blocked", "deleted"}
            allowed_statuses.update(item.value for item in SubscriptionStatus)
            if status_filter not in allowed_statuses:
                return web.json_response({"detail": "Недопустимый статус пользователя"}, status=400)
            conditions.append(User.status == status_filter)

        if promo_group_id:
            try:
                conditions.append(User.promo_group_id == int(promo_group_id))
            except ValueError:
                return web.json_response({"detail": "promo_group_id должен быть числом"}, status=400)

        if has_subscription is not None:
            if has_subscription:
                conditions.append(User.subscription != None)  # noqa: E711
            else:
                conditions.append(User.subscription == None)  # noqa: E711

        if is_trial is not None:
            conditions.append(
                User.subscription.has(Subscription.is_trial.is_(True if is_trial else False))
            )

        sort_mapping = {
            "created_at": User.created_at,
            "last_activity": User.last_activity,
            "balance": User.balance_kopeks,
            "telegram_id": User.telegram_id,
        }
        descending = sort_param.startswith("-")
        sort_key = sort_param[1:] if descending else sort_param
        sort_column = sort_mapping.get(sort_key, User.created_at)
        order_by = sort_column.desc() if descending else sort_column.asc()

        offset = (page - 1) * page_size

        async with AsyncSessionLocal() as session:
            query = (
                select(User)
                .options(
                    selectinload(User.subscription),
                    selectinload(User.promo_group),
                )
                .order_by(order_by)
                .offset(offset)
                .limit(page_size)
            )
            count_query = select(func.count(User.id))

            if conditions:
                query = query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))

            result = await session.execute(query)
            users = result.scalars().all()

            total_result = await session.execute(count_query)
            total_count = int(total_result.scalar() or 0)

        payload = {
            "items": [self._serialize_user(user) for user in users],
            "pagination": get_pagination_info(total_count, page=page, per_page=page_size),
        }
        return web.json_response(payload)

    async def handle_user_detail(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        async with AsyncSessionLocal() as session:
            user = await get_user_by_id(session, user_id)
            if not user:
                return web.json_response({"detail": "Пользователь не найден"}, status=404)

            transactions = await get_user_transactions(session, user.id, limit=10, offset=0)
            total_spent = await get_user_total_spent_kopeks(session, user.id)
            transactions_count = await get_user_transactions_count(session, user.id)

        data = self._serialize_user(user)
        data["transactions"] = [self._serialize_transaction(tx) for tx in transactions]
        data["transactions_summary"] = {
            "count": transactions_count,
            "total_spent_kopeks": total_spent,
            "total_spent_rubles": _rubles(total_spent),
        }
        return web.json_response(data)

    async def handle_user_update(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        update_fields: Dict[str, Any] = {}

        if "status" in payload:
            status_value = payload["status"]
            allowed_statuses = {"active", "blocked", "deleted"}
            allowed_statuses.update(item.value for item in SubscriptionStatus)
            if status_value not in allowed_statuses:
                return web.json_response({"detail": "Недопустимый статус"}, status=400)
            update_fields["status"] = status_value

        for field in ["username", "first_name", "last_name", "language"]:
            if field in payload:
                value = payload[field]
                update_fields[field] = value if value is None else str(value)

        if "promo_group_id" in payload:
            try:
                promo_id = int(payload["promo_group_id"])
            except (TypeError, ValueError):
                return web.json_response({"detail": "promo_group_id должен быть числом"}, status=400)
            update_fields["promo_group_id"] = promo_id

        for flag in ["has_had_paid_subscription", "has_made_first_topup", "auto_promo_group_assigned"]:
            if flag in payload:
                update_fields[flag] = bool(payload[flag])

        if "auto_promo_group_threshold_kopeks" in payload:
            try:
                update_fields["auto_promo_group_threshold_kopeks"] = int(
                    payload["auto_promo_group_threshold_kopeks"]
                )
            except (TypeError, ValueError):
                return web.json_response({"detail": "auto_promo_group_threshold_kopeks должен быть числом"}, status=400)

        async with AsyncSessionLocal() as session:
            user = await get_user_by_id(session, user_id)
            if not user:
                return web.json_response({"detail": "Пользователь не найден"}, status=404)

            if "promo_group_id" in update_fields:
                promo = await session.get(PromoGroup, update_fields["promo_group_id"])
                if not promo:
                    return web.json_response({"detail": "Промогруппа не найдена"}, status=400)

            updated_user = await update_user(session, user, **update_fields)

        return web.json_response(self._serialize_user(updated_user))

    async def handle_user_balance_adjust(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"detail": "Некорректный JSON"}, status=400)

        operation = (payload.get("operation") or "deposit").lower()
        description = payload.get("description") or (
            "Пополнение баланса" if operation == "deposit" else "Списание средств"
        )

        amount_kopeks = payload.get("amount_kopeks")
        if amount_kopeks is None:
            amount_value = payload.get("amount")
            if amount_value is None:
                return web.json_response({"detail": "Укажите amount или amount_kopeks"}, status=400)
            try:
                amount_decimal = Decimal(str(amount_value))
                amount_kopeks = int(amount_decimal * 100)
            except (InvalidOperation, ValueError):
                return web.json_response({"detail": "Некорректное значение amount"}, status=400)

        try:
            amount_kopeks = int(amount_kopeks)
        except (TypeError, ValueError):
            return web.json_response({"detail": "amount_kopeks должен быть числом"}, status=400)

        if amount_kopeks <= 0:
            return web.json_response({"detail": "Сумма должна быть положительной"}, status=400)

        async with AsyncSessionLocal() as session:
            user = await get_user_by_id(session, user_id)
            if not user:
                return web.json_response({"detail": "Пользователь не найден"}, status=404)

            if operation == "withdrawal":
                success = await withdraw_user_balance(session, user, amount_kopeks, description)
            else:
                success = await add_user_balance(session, user, amount_kopeks, description)

            if not success:
                return web.json_response({"detail": "Не удалось изменить баланс"}, status=400)

            updated_user = await get_user_by_id(session, user_id)

        return web.json_response(self._serialize_user(updated_user))

    async def handle_user_transactions(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        try:
            limit = int(request.rel_url.query.get("limit", "20"))
            offset = int(request.rel_url.query.get("offset", "0"))
        except ValueError:
            return web.json_response({"detail": "Некорректные параметры пагинации"}, status=400)

        async with AsyncSessionLocal() as session:
            user = await get_user_by_id(session, user_id)
            if not user:
                return web.json_response({"detail": "Пользователь не найден"}, status=404)

            transactions = await get_user_transactions(session, user.id, limit=limit, offset=offset)
            total = await get_user_transactions_count(session, user.id)

        payload = {
            "items": [self._serialize_transaction(tx) for tx in transactions],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
        }
        return web.json_response(payload)

