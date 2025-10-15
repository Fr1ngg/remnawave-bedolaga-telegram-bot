from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.discount_offer import (
    get_latest_claimed_offer_for_user,
    list_active_discount_offers_for_user,
)
from app.database.crud.promo_group import get_auto_assign_promo_groups
from app.database.crud.rules import get_rules_by_language
from app.database.crud.server_squad import (
    add_user_to_servers,
    get_available_server_squads,
    get_server_ids_by_uuids,
    remove_user_from_servers,
)
from app.database.crud.subscription import (
    add_subscription_servers,
    calculate_subscription_total_cost,
    create_trial_subscription,
    extend_subscription,
    remove_subscription_servers,
    update_subscription_autopay,
)
from app.database.crud.transaction import (
    create_transaction,
    get_user_total_spent_kopeks,
)
from app.database.crud.user import get_user_by_telegram_id, subtract_user_balance
from app.database.models import (
    PromoGroup,
    Subscription,
    SubscriptionTemporaryAccess,
    Transaction,
    TransactionType,
    User,
)
from app.services.admin_notification_service import AdminNotificationService
from app.services.faq_service import FaqService
from app.services.privacy_policy_service import PrivacyPolicyService
from app.services.public_offer_service import PublicOfferService
from app.services.remnawave_service import (
    RemnaWaveConfigurationError,
    RemnaWaveService,
)
from app.services.subscription_service import SubscriptionService
from app.services.subscription_purchase_service import (
    purchase_service,
    PurchaseBalanceError,
    PurchaseValidationError,
)
from app.utils.subscription_utils import get_happ_cryptolink_redirect_link
from app.utils.telegram_webapp import (
    TelegramWebAppAuthError,
    parse_webapp_init_data,
)
from app.utils.user_utils import (
    get_detailed_referral_list,
    get_user_referral_summary,
)
from app.utils.pricing_utils import (
    apply_percentage_discount,
    calculate_months_from_days,
    calculate_prorated_price,
    format_period_description,
    get_remaining_months,
    validate_pricing_calculation,
)
from app.utils.promo_offer import get_user_active_promo_discount_percent

from ...dependencies import get_db_session
from ...schemas.miniapp import (
    MiniAppAutoPromoGroupLevel,
    MiniAppConnectedServer,
    MiniAppDevice,
    MiniAppDeviceRemovalRequest,
    MiniAppDeviceRemovalResponse,
    MiniAppFaq,
    MiniAppFaqItem,
    MiniAppLegalDocuments,
    MiniAppPromoGroup,
    MiniAppReferralInfo,
    MiniAppReferralItem,
    MiniAppReferralList,
    MiniAppReferralRecentEarning,
    MiniAppReferralStats,
    MiniAppReferralTerms,
    MiniAppRichTextDocument,
    MiniAppSubscriptionRequest,
    MiniAppSubscriptionResponse,
    MiniAppSubscriptionUser,
    MiniAppTransaction,
    MiniAppSubscriptionSettingsRequest,
    MiniAppSubscriptionSettingsResponse,
    MiniAppSubscriptionSettings,
    MiniAppSubscriptionCurrentSettings,
    MiniAppSubscriptionServersSettings,
    MiniAppSubscriptionServerOption,
    MiniAppSubscriptionTrafficSettings,
    MiniAppSubscriptionTrafficOption,
    MiniAppSubscriptionDevicesSettings,
    MiniAppSubscriptionDeviceOption,
    MiniAppSubscriptionBillingContext,
    MiniAppSubscriptionServersUpdateRequest,
    MiniAppSubscriptionTrafficUpdateRequest,
    MiniAppSubscriptionDevicesUpdateRequest,
    MiniAppSubscriptionUpdateResponse,
    MiniAppSubscriptionPurchaseOptionsRequest,
    MiniAppSubscriptionPurchaseOptionsResponse,
    MiniAppSubscriptionPurchasePreviewRequest,
    MiniAppSubscriptionPurchasePreviewResponse,
    MiniAppSubscriptionPurchaseRequest,
    MiniAppSubscriptionPurchaseResponse,
    MiniAppSubscriptionTrialRequest,
    MiniAppSubscriptionTrialResponse,
    MiniAppSubscriptionAutopay,
    MiniAppSubscriptionAutopayRequest,
    MiniAppSubscriptionAutopayResponse,
    MiniAppSubscriptionRenewalOptionsRequest,
    MiniAppSubscriptionRenewalOptionsResponse,
    MiniAppSubscriptionRenewalPeriod,
    MiniAppSubscriptionRenewalRequest,
    MiniAppSubscriptionRenewalResponse,
)

from .promo import (
    ActiveOfferContext,
    build_promo_offer_models,
    extract_promo_discounts,
    find_active_test_access_offers,
    resolve_connected_servers,
)


logger = logging.getLogger(__name__)

router = APIRouter()


async def _with_admin_notification_service(
    handler: Callable[[AdminNotificationService], Awaitable[Any]],
) -> None:
    if not getattr(settings, "ADMIN_NOTIFICATIONS_ENABLED", False):
        return
    if not settings.BOT_TOKEN:
        logger.debug("Skipping admin notification: bot token is not configured")
        return

    bot: Bot | None = None
    try:
        bot = Bot(token=settings.BOT_TOKEN)
        service = AdminNotificationService(bot)
        await handler(service)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error("Failed to send admin notification from miniapp: %s", error)
    finally:
        if bot:
            await bot.session.close()


def _merge_purchase_selection_from_request(
    payload: Union[
        "MiniAppSubscriptionPurchasePreviewRequest",
        "MiniAppSubscriptionPurchaseRequest",
    ]
) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    if payload.selection:
        base.update(payload.selection)

    def _maybe_set(key: str, value: Any) -> None:
        if value is None:
            return
        if key not in base:
            base[key] = value

    _maybe_set("period_id", getattr(payload, "period_id", None))
    _maybe_set("period_days", getattr(payload, "period_days", None))

    _maybe_set("traffic_value", getattr(payload, "traffic_value", None))
    _maybe_set("traffic", getattr(payload, "traffic", None))
    _maybe_set("traffic_gb", getattr(payload, "traffic_gb", None))

    servers = getattr(payload, "servers", None)
    if servers is not None and "servers" not in base:
        base["servers"] = servers
    countries = getattr(payload, "countries", None)
    if countries is not None and "countries" not in base:
        base["countries"] = countries
    server_uuids = getattr(payload, "server_uuids", None)
    if server_uuids is not None and "server_uuids" not in base:
        base["server_uuids"] = server_uuids

    _maybe_set("devices", getattr(payload, "devices", None))
    _maybe_set("device_limit", getattr(payload, "device_limit", None))

    return base


def _format_gb(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_gb_label(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"{value:.0f} GB"
    if absolute >= 10:
        return f"{value:.1f} GB"
    return f"{value:.2f} GB"


def _format_limit_label(limit: Optional[int]) -> str:
    if not limit:
        return "Unlimited"
    return f"{limit} GB"


async def _resolve_user_from_init_data(
    db: AsyncSession,
    init_data: str,
) -> Tuple[User, Dict[str, Any]]:
    if not init_data:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing initData",
        )

    try:
        webapp_data = parse_webapp_init_data(init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user payload",
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user identifier",
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user, webapp_data


def _normalize_amount_kopeks(
    amount_rubles: Optional[float],
    amount_kopeks: Optional[int],
) -> Optional[int]:
    if amount_kopeks is not None:
        try:
            normalized = int(amount_kopeks)
        except (TypeError, ValueError):
            return None
        return normalized if normalized >= 0 else None

    if amount_rubles is None:
        return None

    try:
        decimal_amount = Decimal(str(amount_rubles)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return None

    normalized = int((decimal_amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return normalized if normalized >= 0 else None


@router.post("/subscription", response_model=MiniAppSubscriptionResponse)
async def get_subscription_details(
    payload: MiniAppSubscriptionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionResponse:
    try:
        webapp_data = parse_webapp_init_data(payload.init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user payload",
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user identifier",
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    purchase_url = (settings.MINIAPP_PURCHASE_URL or "").strip()

    if not user:
        detail: Dict[str, Any] = {
            "code": "user_not_found",
            "message": "User not found. Please register in the bot to continue.",
            "title": "Registration required",
        }
        if purchase_url:
            detail["purchase_url"] = purchase_url
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )

    subscription = getattr(user, "subscription", None)
    usage_synced = False

    if subscription and _is_remnawave_configured():
        service = SubscriptionService()
        try:
            usage_synced = await service.sync_subscription_usage(db, subscription)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to sync subscription usage for user %s: %s",
                getattr(user, "id", "unknown"),
                error,
            )

    if usage_synced:
        try:
            await db.refresh(subscription, attribute_names=["traffic_used_gb", "updated_at"])
        except Exception as refresh_error:  # pragma: no cover - defensive logging
            logger.debug(
                "Failed to refresh subscription after usage sync: %s",
                refresh_error,
            )

        try:
            await db.refresh(user)
        except Exception as refresh_error:  # pragma: no cover - defensive logging
            logger.debug(
                "Failed to refresh user after usage sync: %s",
                refresh_error,
            )
            user = await get_user_by_telegram_id(db, telegram_id)

        subscription = getattr(user, "subscription", subscription)
    lifetime_used = _bytes_to_gb(getattr(user, "lifetime_used_traffic_bytes", 0))

    transactions_query = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(10)
    )
    transactions_result = await db.execute(transactions_query)
    transactions = list(transactions_result.scalars().all())

    balance_currency = getattr(user, "balance_currency", None)
    if isinstance(balance_currency, str):
        balance_currency = balance_currency.upper()

    promo_group = getattr(user, "promo_group", None)
    total_spent_kopeks = await get_user_total_spent_kopeks(db, user.id)
    auto_assign_groups = await get_auto_assign_promo_groups(db)

    auto_promo_levels: List[MiniAppAutoPromoGroupLevel] = []
    for group in auto_assign_groups:
        threshold = group.auto_assign_total_spent_kopeks or 0
        if threshold <= 0:
            continue

        auto_promo_levels.append(
            MiniAppAutoPromoGroupLevel(
                id=group.id,
                name=group.name,
                threshold_kopeks=threshold,
                threshold_rubles=round(threshold / 100, 2),
                threshold_label=settings.format_price(threshold),
                is_reached=total_spent_kopeks >= threshold,
                is_current=bool(promo_group and promo_group.id == group.id),
                **extract_promo_discounts(group),
            )
        )

    active_discount_percent = 0
    try:
        active_discount_percent = int(getattr(user, "promo_offer_discount_percent", 0) or 0)
    except (TypeError, ValueError):
        active_discount_percent = 0

    active_discount_expires_at = getattr(user, "promo_offer_discount_expires_at", None)
    now = datetime.utcnow()
    if active_discount_expires_at and active_discount_expires_at <= now:
        active_discount_expires_at = None
        active_discount_percent = 0

    available_promo_offers = await list_active_discount_offers_for_user(db, user.id)

    promo_offer_source = getattr(user, "promo_offer_discount_source", None)
    active_offer_contexts: List[ActiveOfferContext] = []
    if promo_offer_source or active_discount_percent > 0:
        active_discount_offer = await get_latest_claimed_offer_for_user(
            db,
            user.id,
            promo_offer_source,
        )
        if active_discount_offer and active_discount_percent > 0:
            active_offer_contexts.append(
                (
                    active_discount_offer,
                    active_discount_percent,
                    active_discount_expires_at,
                )
            )

    if subscription:
        active_offer_contexts.extend(
            await find_active_test_access_offers(db, subscription)
        )

    promo_offers = await build_promo_offer_models(
        db,
        available_promo_offers,
        active_offer_contexts,
        user=user,
    )

    content_language_preference = user.language or settings.DEFAULT_LANGUAGE or "ru"

    def _normalize_language_code(language: Optional[str]) -> str:
        base_language = language or settings.DEFAULT_LANGUAGE or "ru"
        return base_language.split("-")[0].lower()

    faq_payload: Optional[MiniAppFaq] = None
    requested_faq_language = FaqService.normalize_language(content_language_preference)
    faq_pages = await FaqService.get_pages(
        db,
        requested_faq_language,
        include_inactive=False,
        fallback=True,
    )

    if faq_pages:
        faq_setting = await FaqService.get_setting(
            db,
            requested_faq_language,
            fallback=True,
        )
        is_enabled = bool(faq_setting.is_enabled) if faq_setting else True

        if is_enabled:
            ordered_pages = sorted(
                faq_pages,
                key=lambda page: (
                    (page.display_order or 0),
                    page.id,
                ),
            )
            faq_items: List[MiniAppFaqItem] = []
            for page in ordered_pages:
                raw_content = (page.content or "").strip()
                if not raw_content:
                    continue
                if not re.sub(r"<[^>]+>", "", raw_content).strip():
                    continue
                faq_items.append(
                    MiniAppFaqItem(
                        id=page.id,
                        title=page.title or None,
                        content=page.content or "",
                        display_order=getattr(page, "display_order", None),
                    )
            )

            if faq_items:
                resolved_language = (
                    faq_setting.language
                    if faq_setting and faq_setting.language
                    else ordered_pages[0].language
                )
                faq_payload = MiniAppFaq(
                    requested_language=requested_faq_language,
                    language=resolved_language or requested_faq_language,
                    is_enabled=is_enabled,
                    total=len(faq_items),
                    items=faq_items,
                )

    legal_documents_payload: Optional[MiniAppLegalDocuments] = None

    requested_offer_language = PublicOfferService.normalize_language(content_language_preference)
    public_offer = await PublicOfferService.get_active_offer(
        db,
        requested_offer_language,
    )
    if public_offer and (public_offer.content or "").strip():
        legal_documents_payload = legal_documents_payload or MiniAppLegalDocuments()
        legal_documents_payload.public_offer = MiniAppRichTextDocument(
            requested_language=requested_offer_language,
            language=public_offer.language,
            title=None,
            is_enabled=bool(public_offer.is_enabled),
            content=public_offer.content or "",
            created_at=public_offer.created_at,
            updated_at=public_offer.updated_at,
        )

    requested_policy_language = PrivacyPolicyService.normalize_language(
        content_language_preference
    )
    privacy_policy = await PrivacyPolicyService.get_active_policy(
        db,
        requested_policy_language,
    )
    if privacy_policy and (privacy_policy.content or "").strip():
        legal_documents_payload = legal_documents_payload or MiniAppLegalDocuments()
        legal_documents_payload.privacy_policy = MiniAppRichTextDocument(
            requested_language=requested_policy_language,
            language=privacy_policy.language,
            title=None,
            is_enabled=bool(privacy_policy.is_enabled),
            content=privacy_policy.content or "",
            created_at=privacy_policy.created_at,
            updated_at=privacy_policy.updated_at,
        )

    requested_rules_language = _normalize_language_code(content_language_preference)
    default_rules_language = _normalize_language_code(settings.DEFAULT_LANGUAGE)
    service_rules = await get_rules_by_language(db, requested_rules_language)
    if not service_rules and requested_rules_language != default_rules_language:
        service_rules = await get_rules_by_language(db, default_rules_language)

    if service_rules and (service_rules.content or "").strip():
        legal_documents_payload = legal_documents_payload or MiniAppLegalDocuments()
        legal_documents_payload.service_rules = MiniAppRichTextDocument(
            requested_language=requested_rules_language,
            language=service_rules.language,
            title=getattr(service_rules, "title", None),
            is_enabled=bool(getattr(service_rules, "is_active", True)),
            content=service_rules.content or "",
            created_at=getattr(service_rules, "created_at", None),
            updated_at=getattr(service_rules, "updated_at", None),
        )

    links_payload: Dict[str, Any] = {}
    connected_squads: List[str] = []
    connected_servers: List[MiniAppConnectedServer] = []
    links: List[str] = []
    ss_conf_links: Dict[str, str] = {}
    subscription_url: Optional[str] = None
    subscription_crypto_link: Optional[str] = None
    happ_redirect_link: Optional[str] = None
    remnawave_short_uuid: Optional[str] = None
    status_actual = "missing"
    subscription_status_value = "none"
    traffic_used_value = 0.0
    traffic_limit_value = 0
    device_limit_value: Optional[int] = settings.DEFAULT_DEVICE_LIMIT or None
    autopay_enabled = False

    if subscription:
        traffic_used_value = _format_gb(subscription.traffic_used_gb)
        traffic_limit_value = subscription.traffic_limit_gb or 0
        status_actual = subscription.actual_status
        subscription_status_value = subscription.status
        links_payload = await _load_subscription_links(subscription)
        subscription_url = (
            links_payload.get("subscription_url") or subscription.subscription_url
        )
        subscription_crypto_link = (
            links_payload.get("happ_crypto_link")
            or subscription.subscription_crypto_link
        )
        happ_redirect_link = get_happ_cryptolink_redirect_link(subscription_crypto_link)
        connected_squads = list(subscription.connected_squads or [])
        connected_servers = await resolve_connected_servers(db, connected_squads)
        links = links_payload.get("links") or connected_squads
        ss_conf_links = links_payload.get("ss_conf_links") or {}
        remnawave_short_uuid = subscription.remnawave_short_uuid
        device_limit_value = subscription.device_limit
        autopay_enabled = bool(subscription.autopay_enabled)

    autopay_payload = _build_autopay_payload(subscription)
    autopay_days_before = (
        getattr(autopay_payload, "autopay_days_before", None)
        if autopay_payload
        else None
    )
    autopay_days_options = (
        list(getattr(autopay_payload, "autopay_days_options", []) or [])
        if autopay_payload
        else []
    )
    autopay_extras = _autopay_response_extras(
        autopay_enabled,
        autopay_days_before,
        autopay_days_options,
        autopay_payload,
    )

    devices_count, devices = await _load_devices_info(user)

    response_user = MiniAppSubscriptionUser(
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=_resolve_display_name(
            {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "telegram_id": user.telegram_id,
            }
        ),
        language=user.language,
        status=user.status,
        subscription_status=subscription_status_value,
        subscription_actual_status=status_actual,
        status_label=_status_label(status_actual),
        expires_at=getattr(subscription, "end_date", None),
        device_limit=device_limit_value,
        traffic_used_gb=round(traffic_used_value, 2),
        traffic_used_label=_format_gb_label(traffic_used_value),
        traffic_limit_gb=traffic_limit_value,
        traffic_limit_label=_format_limit_label(traffic_limit_value),
        lifetime_used_traffic_gb=lifetime_used,
        has_active_subscription=status_actual in {"active", "trial"},
        promo_offer_discount_percent=active_discount_percent,
        promo_offer_discount_expires_at=active_discount_expires_at,
        promo_offer_discount_source=promo_offer_source,
    )

    referral_info = await _build_referral_info(db, user)

    trial_available = _is_trial_available_for_user(user)
    trial_duration_days = (
        settings.TRIAL_DURATION_DAYS if settings.TRIAL_DURATION_DAYS > 0 else None
    )

    subscription_missing_reason = None
    if subscription is None:
        if not trial_available and settings.TRIAL_DURATION_DAYS > 0:
            subscription_missing_reason = "trial_expired"
        else:
            subscription_missing_reason = "not_found"

    return MiniAppSubscriptionResponse(
        subscription_id=getattr(subscription, "id", None),
        remnawave_short_uuid=remnawave_short_uuid,
        user=response_user,
        subscription_url=subscription_url,
        subscription_crypto_link=subscription_crypto_link,
        subscription_purchase_url=purchase_url or None,
        links=links,
        ss_conf_links=ss_conf_links,
        connected_squads=connected_squads,
        connected_servers=connected_servers,
        connected_devices_count=devices_count,
        connected_devices=devices,
        happ=links_payload.get("happ") if subscription else None,
        happ_link=links_payload.get("happ_link") if subscription else None,
        happ_crypto_link=links_payload.get("happ_crypto_link") if subscription else None,
        happ_cryptolink_redirect_link=happ_redirect_link,
        happ_cryptolink_redirect_template=settings.get_happ_cryptolink_redirect_template(),
        balance_kopeks=user.balance_kopeks,
        balance_rubles=round(user.balance_rubles, 2),
        balance_currency=balance_currency,
        transactions=[_serialize_transaction(tx) for tx in transactions],
        promo_offers=promo_offers,
        promo_group=(
            MiniAppPromoGroup(
                id=promo_group.id,
                name=promo_group.name,
                **extract_promo_discounts(promo_group),
            )
            if promo_group
            else None
        ),
        auto_assign_promo_groups=auto_promo_levels,
        total_spent_kopeks=total_spent_kopeks,
        total_spent_rubles=round(total_spent_kopeks / 100, 2),
        total_spent_label=settings.format_price(total_spent_kopeks),
        subscription_type=(
            "trial"
            if subscription and subscription.is_trial
            else ("paid" if subscription else "none")
        ),
        autopay_enabled=autopay_enabled,
        autopay_days_before=autopay_days_before,
        autopay_days_options=autopay_days_options,
        autopay=autopay_payload,
        autopay_settings=autopay_payload,
        branding=settings.get_miniapp_branding(),
        faq=faq_payload,
        legal_documents=legal_documents_payload,
        referral=referral_info,
        subscription_missing=subscription is None,
        subscription_missing_reason=subscription_missing_reason,
        trial_available=trial_available,
        trial_duration_days=trial_duration_days,
        trial_status="available" if trial_available else "unavailable",
        **autopay_extras,
    )


@router.post(
    "/subscription/autopay",
    response_model=MiniAppSubscriptionAutopayResponse,
)
async def update_subscription_autopay_endpoint(
    payload: MiniAppSubscriptionAutopayRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionAutopayResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)

    target_enabled = (
        bool(payload.enabled)
        if payload.enabled is not None
        else bool(subscription.autopay_enabled)
    )

    requested_days = payload.days_before
    normalized_days = _normalize_autopay_days(requested_days)
    current_days = _normalize_autopay_days(
        getattr(subscription, "autopay_days_before", None)
    )
    if normalized_days is None:
        normalized_days = current_days

    options = _get_autopay_day_options(subscription)
    default_day = _normalize_autopay_days(
        getattr(settings, "DEFAULT_AUTOPAY_DAYS_BEFORE", None)
    )
    if default_day is None and options:
        default_day = options[0]

    if target_enabled and normalized_days is None:
        if default_day is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "autopay_no_days",
                    "message": "Auto-pay day selection is temporarily unavailable",
                },
            )
        normalized_days = default_day

    if normalized_days is None:
        normalized_days = default_day or (options[0] if options else 1)

    if (
        bool(subscription.autopay_enabled) == target_enabled
        and current_days == normalized_days
    ):
        autopay_payload = _build_autopay_payload(subscription)
        autopay_days_before = (
            getattr(autopay_payload, "autopay_days_before", None)
            if autopay_payload
            else None
        )
        autopay_days_options = (
            list(getattr(autopay_payload, "autopay_days_options", []) or [])
            if autopay_payload
            else options
        )
        extras = _autopay_response_extras(
            target_enabled,
            autopay_days_before,
            autopay_days_options,
            autopay_payload,
        )
        return MiniAppSubscriptionAutopayResponse(
            subscription_id=subscription.id,
            autopay_enabled=target_enabled,
            autopay_days_before=autopay_days_before,
            autopay_days_options=autopay_days_options,
            autopay=autopay_payload,
            autopay_settings=autopay_payload,
            **extras,
        )

    updated_subscription = await update_subscription_autopay(
        db,
        subscription,
        target_enabled,
        normalized_days,
    )

    autopay_payload = _build_autopay_payload(updated_subscription)
    autopay_days_before = (
        getattr(autopay_payload, "autopay_days_before", None)
        if autopay_payload
        else None
    )
    autopay_days_options = (
        list(getattr(autopay_payload, "autopay_days_options", []) or [])
        if autopay_payload
        else _get_autopay_day_options(updated_subscription)
    )
    extras = _autopay_response_extras(
        bool(updated_subscription.autopay_enabled),
        autopay_days_before,
        autopay_days_options,
        autopay_payload,
    )

    return MiniAppSubscriptionAutopayResponse(
        subscription_id=updated_subscription.id,
        autopay_enabled=bool(updated_subscription.autopay_enabled),
        autopay_days_before=autopay_days_before,
        autopay_days_options=autopay_days_options,
        autopay=autopay_payload,
        autopay_settings=autopay_payload,
        **extras,
    )


@router.post(
    "/subscription/trial",
    response_model=MiniAppSubscriptionTrialResponse,
)
async def activate_subscription_trial_endpoint(
    payload: MiniAppSubscriptionTrialRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionTrialResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)

    existing_subscription = getattr(user, "subscription", None)
    if existing_subscription is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "subscription_exists",
                "message": "Subscription is already active",
            },
        )

    if not _is_trial_available_for_user(user):
        error_code = "trial_unavailable"
        if getattr(user, "has_had_paid_subscription", False):
            error_code = "trial_expired"
        elif settings.TRIAL_DURATION_DAYS <= 0:
            error_code = "trial_disabled"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": error_code,
                "message": "Trial is not available for this user",
            },
        )

    try:
        subscription = await create_trial_subscription(db, user.id)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to activate trial subscription for user %s: %s",
            user.id,
            error,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "trial_activation_failed",
                "message": "Failed to activate trial subscription",
            },
        ) from error

    await db.refresh(user)
    await db.refresh(subscription)

    subscription_service = SubscriptionService()
    try:
        await subscription_service.create_remnawave_user(db, subscription)
    except RemnaWaveConfigurationError as error:  # pragma: no cover - configuration issues
        logger.warning("RemnaWave update skipped: %s", error)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to create RemnaWave user for trial subscription %s: %s",
            subscription.id,
            error,
        )

    await db.refresh(subscription)

    duration_days: Optional[int] = None
    if subscription.start_date and subscription.end_date:
        try:
            duration_days = max(
                0,
                (subscription.end_date.date() - subscription.start_date.date()).days,
            )
        except Exception:  # pragma: no cover - defensive fallback
            duration_days = None

    if not duration_days and settings.TRIAL_DURATION_DAYS > 0:
        duration_days = settings.TRIAL_DURATION_DAYS

    language_code = _normalize_language_code(user)
    if language_code == "ru":
        if duration_days:
            message = f"Триал активирован на {duration_days} дн. Приятного пользования!"
        else:
            message = "Триал активирован. Приятного пользования!"
    else:
        if duration_days:
            message = f"Trial activated for {duration_days} days. Enjoy!"
        else:
            message = "Trial activated successfully. Enjoy!"

    await _with_admin_notification_service(
        lambda service: service.send_trial_activation_notification(db, user, subscription)
    )

    return MiniAppSubscriptionTrialResponse(
        message=message,
        subscription_id=getattr(subscription, "id", None),
        trial_status="activated",
        trial_duration_days=duration_days,
    )


@router.post("/subscription/renewal/options",
    response_model=MiniAppSubscriptionRenewalOptionsResponse,
)
async def get_subscription_renewal_options_endpoint(
    payload: MiniAppSubscriptionRenewalOptionsRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionRenewalOptionsResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)

    periods, pricing_map, default_period_id = await _prepare_subscription_renewal_options(
        db,
        user,
        subscription,
    )

    balance_kopeks = getattr(user, "balance_kopeks", 0)
    currency = (getattr(user, "balance_currency", None) or "RUB").upper()

    promo_group = getattr(user, "promo_group", None)
    promo_group_model = (
        MiniAppPromoGroup(
            id=promo_group.id,
            name=promo_group.name,
            **extract_promo_discounts(promo_group),
        )
        if promo_group
        else None
    )

    promo_offer_payload = _build_promo_offer_payload(user)

    missing_amount = None
    if default_period_id and default_period_id in pricing_map:
        selected_pricing = pricing_map[default_period_id]
        final_total = selected_pricing.get("final_total")
        if isinstance(final_total, int) and balance_kopeks < final_total:
            missing_amount = final_total - balance_kopeks

    renewal_autopay_payload = _build_autopay_payload(subscription)
    renewal_autopay_days_before = (
        getattr(renewal_autopay_payload, "autopay_days_before", None)
        if renewal_autopay_payload
        else None
    )
    renewal_autopay_days_options = (
        list(getattr(renewal_autopay_payload, "autopay_days_options", []) or [])
        if renewal_autopay_payload
        else []
    )
    renewal_autopay_extras = _autopay_response_extras(
        bool(subscription.autopay_enabled),
        renewal_autopay_days_before,
        renewal_autopay_days_options,
        renewal_autopay_payload,
    )

    return MiniAppSubscriptionRenewalOptionsResponse(
        subscription_id=subscription.id,
        currency=currency,
        balance_kopeks=balance_kopeks,
        balance_label=settings.format_price(balance_kopeks),
        promo_group=promo_group_model,
        promo_offer=promo_offer_payload,
        periods=periods,
        default_period_id=default_period_id,
        missing_amount_kopeks=missing_amount,
        status_message=_build_renewal_status_message(user),
        autopay_enabled=bool(subscription.autopay_enabled),
        autopay_days_before=renewal_autopay_days_before,
        autopay_days_options=renewal_autopay_days_options,
        autopay=renewal_autopay_payload,
        autopay_settings=renewal_autopay_payload,
        **renewal_autopay_extras,
    )


@router.post(
    "/subscription/renewal",
    response_model=MiniAppSubscriptionRenewalResponse,
)
async def submit_subscription_renewal_endpoint(
    payload: MiniAppSubscriptionRenewalRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionRenewalResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)

    period_days: Optional[int] = None
    if payload.period_days is not None:
        try:
            period_days = int(payload.period_days)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_period", "message": "Invalid renewal period"},
            ) from error

    if period_days is None:
        period_days = _parse_period_identifier(payload.period_id)

    if period_days is None or period_days <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_period", "message": "Invalid renewal period"},
        )

    available_periods = [
        period for period in settings.get_available_renewal_periods() if period > 0
    ]
    if period_days not in available_periods:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "period_unavailable", "message": "Selected renewal period is not available"},
        )

    try:
        pricing = await _calculate_subscription_renewal_pricing(
            db,
            user,
            subscription,
            period_days,
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            "Failed to calculate renewal pricing for subscription %s (period %s): %s",
            subscription.id,
            period_days,
            error,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={"code": "pricing_failed", "message": "Failed to calculate renewal pricing"},
        ) from error

    final_total = int(pricing.get("final_total") or 0)
    balance_kopeks = getattr(user, "balance_kopeks", 0)

    if final_total > 0 and balance_kopeks < final_total:
        missing = final_total - balance_kopeks
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "insufficient_funds",
                "message": "Not enough funds to renew the subscription",
                "missing_amount_kopeks": missing,
            },
        )

    consume_promo_offer = bool(pricing.get("promo_discount_value"))
    description = f"Продление подписки на {period_days} дней"
    old_end_date = subscription.end_date

    if final_total > 0 or consume_promo_offer:
        success = await subtract_user_balance(
            db,
            user,
            final_total,
            description,
            consume_promo_offer=consume_promo_offer,
        )
        if not success:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "charge_failed", "message": "Failed to charge balance"},
            )
        await db.refresh(user)

    subscription = await extend_subscription(db, subscription, period_days)

    server_ids = pricing.get("server_ids") or []
    server_prices_for_period = pricing.get("details", {}).get(
        "servers_individual_prices",
        [],
    )
    if server_ids:
        try:
            await add_subscription_servers(
                db,
                subscription,
                server_ids,
                server_prices_for_period,
            )
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to record renewal server prices for subscription %s: %s",
                subscription.id,
                error,
            )

    subscription_service = SubscriptionService()
    try:
        await subscription_service.update_remnawave_user(
            db,
            subscription,
            reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
            reset_reason="subscription renewal",
        )
    except RemnaWaveConfigurationError as error:  # pragma: no cover - configuration issues
        logger.warning("RemnaWave update skipped: %s", error)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to update RemnaWave user for subscription %s: %s",
            subscription.id,
            error,
        )

    transaction: Optional[Transaction] = None
    try:
        transaction = await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=final_total,
            description=description,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to create renewal transaction for subscription %s: %s",
            subscription.id,
            error,
        )

    await db.refresh(user)
    await db.refresh(subscription)

    if transaction and old_end_date and subscription.end_date:
        await _with_admin_notification_service(
            lambda service: service.send_subscription_extension_notification(
                db,
                user,
                subscription,
                transaction,
                period_days,
                old_end_date,
                new_end_date=subscription.end_date,
                balance_after=user.balance_kopeks,
            )
        )

    language_code = _normalize_language_code(user)
    amount_label = settings.format_price(final_total)
    date_label = (
        subscription.end_date.strftime("%d.%m.%Y %H:%M")
        if subscription.end_date
        else ""
    )

    if language_code == "ru":
        if final_total > 0:
            message = (
                f"Подписка продлена до {date_label}. " if date_label else "Подписка продлена. "
            ) + f"Списано {amount_label}."
        else:
            message = (
                f"Подписка продлена до {date_label}."
                if date_label
                else "Подписка успешно продлена."
            )
    else:
        if final_total > 0:
            message = (
                f"Subscription renewed until {date_label}. " if date_label else "Subscription renewed. "
            ) + f"Charged {amount_label}."
        else:
            message = (
                f"Subscription renewed until {date_label}."
                if date_label
                else "Subscription renewed successfully."
            )

    promo_discount_value = pricing.get("promo_discount_value") or 0
    if consume_promo_offer and promo_discount_value > 0:
        discount_label = settings.format_price(promo_discount_value)
        if language_code == "ru":
            message += f" Применена дополнительная скидка {discount_label}."
        else:
            message += f" Promo discount applied: {discount_label}."

    return MiniAppSubscriptionRenewalResponse(
        message=message,
        balance_kopeks=user.balance_kopeks,
        balance_label=settings.format_price(user.balance_kopeks),
        subscription_id=subscription.id,
        renewed_until=subscription.end_date,
    )


@router.post(
    "/subscription/purchase/options",
    response_model=MiniAppSubscriptionPurchaseOptionsResponse,
)
async def get_subscription_purchase_options_endpoint(
    payload: MiniAppSubscriptionPurchaseOptionsRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionPurchaseOptionsResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    context = await purchase_service.build_options(db, user)

    data_payload = dict(context.payload)
    data_payload.setdefault("currency", context.currency)
    data_payload.setdefault("balance_kopeks", context.balance_kopeks)
    data_payload.setdefault("balanceKopeks", context.balance_kopeks)
    data_payload.setdefault("balance_label", settings.format_price(context.balance_kopeks))
    data_payload.setdefault("balanceLabel", settings.format_price(context.balance_kopeks))

    return MiniAppSubscriptionPurchaseOptionsResponse(
        currency=context.currency,
        balance_kopeks=context.balance_kopeks,
        balance_label=settings.format_price(context.balance_kopeks),
        subscription_id=data_payload.get("subscription_id") or data_payload.get("subscriptionId"),
        data=data_payload,
    )


@router.post(
    "/subscription/purchase/preview",
    response_model=MiniAppSubscriptionPurchasePreviewResponse,
)
async def subscription_purchase_preview_endpoint(
    payload: MiniAppSubscriptionPurchasePreviewRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionPurchasePreviewResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    context = await purchase_service.build_options(db, user)

    selection_payload = _merge_purchase_selection_from_request(payload)
    try:
        selection = purchase_service.parse_selection(context, selection_payload)
    except PurchaseValidationError as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": str(error)},
        ) from error

    pricing = await purchase_service.calculate_pricing(db, context, selection)
    preview_payload = purchase_service.build_preview_payload(context, pricing)

    balance_label = settings.format_price(getattr(user, "balance_kopeks", 0))

    return MiniAppSubscriptionPurchasePreviewResponse(
        preview=preview_payload,
        balance_kopeks=user.balance_kopeks,
        balance_label=balance_label,
    )


@router.post(
    "/subscription/purchase",
    response_model=MiniAppSubscriptionPurchaseResponse,
)
async def subscription_purchase_endpoint(
    payload: MiniAppSubscriptionPurchaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionPurchaseResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    context = await purchase_service.build_options(db, user)

    selection_payload = _merge_purchase_selection_from_request(payload)
    try:
        selection = purchase_service.parse_selection(context, selection_payload)
    except PurchaseValidationError as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": str(error)},
        ) from error

    pricing = await purchase_service.calculate_pricing(db, context, selection)

    try:
        result = await purchase_service.submit_purchase(db, context, pricing)
    except PurchaseBalanceError as error:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "insufficient_funds", "message": str(error)},
        ) from error
    except PurchaseValidationError as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": str(error)},
        ) from error

    await db.refresh(user)

    subscription = result.get("subscription")
    transaction = result.get("transaction")
    was_trial_conversion = bool(result.get("was_trial_conversion"))
    period_days = getattr(getattr(pricing, "selection", None), "period", None)
    period_days = getattr(period_days, "days", None) if period_days else None

    if subscription is not None:
        try:
            await db.refresh(subscription)
        except Exception:  # pragma: no cover - defensive refresh safeguard
            pass

    if subscription and transaction and period_days:
        await _with_admin_notification_service(
            lambda service: service.send_subscription_purchase_notification(
                db,
                user,
                subscription,
                transaction,
                period_days,
                was_trial_conversion=was_trial_conversion,
            )
        )

    balance_label = settings.format_price(getattr(user, "balance_kopeks", 0))

    return MiniAppSubscriptionPurchaseResponse(
        message=result.get("message"),
        balance_kopeks=user.balance_kopeks,
        balance_label=balance_label,
        subscription_id=getattr(subscription, "id", None),
    )


@router.post(
    "/subscription/settings",
    response_model=MiniAppSubscriptionSettingsResponse,
)
async def get_subscription_settings_endpoint(
    payload: MiniAppSubscriptionSettingsRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionSettingsResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)

    settings_payload = await _build_subscription_settings(db, user, subscription)

    return MiniAppSubscriptionSettingsResponse(settings=settings_payload)


@router.post(
    "/subscription/servers",
    response_model=MiniAppSubscriptionUpdateResponse,
)
async def update_subscription_servers_endpoint(
    payload: MiniAppSubscriptionServersUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionUpdateResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)
    old_servers = list(getattr(subscription, "connected_squads", []) or [])

    raw_selection: List[str] = []
    for collection in (
        payload.servers,
        payload.squads,
        payload.server_uuids,
        payload.squad_uuids,
    ):
        if collection:
            raw_selection.extend(collection)

    selected_order: List[str] = []
    seen: set[str] = set()
    for item in raw_selection:
        if not item:
            continue
        uuid = str(item).strip()
        if not uuid or uuid in seen:
            continue
        seen.add(uuid)
        selected_order.append(uuid)

    if not selected_order:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "validation_error",
                "message": "At least one server must be selected",
            },
        )

    current_squads = list(subscription.connected_squads or [])
    current_set = set(current_squads)
    selected_set = set(selected_order)

    added = [uuid for uuid in selected_order if uuid not in current_set]
    removed = [uuid for uuid in current_squads if uuid not in selected_set]

    if not added and not removed:
        return MiniAppSubscriptionUpdateResponse(
            success=True,
            message="No changes",
        )

    period_hint_days = _get_period_hint_from_subscription(subscription)
    servers_discount = _get_addon_discount_percent_for_user(
        user,
        "servers",
        period_hint_days,
    )

    _, _, catalog = await _prepare_server_catalog(
        db,
        user,
        subscription,
        servers_discount,
    )

    invalid_servers = [uuid for uuid in selected_order if uuid not in catalog]
    if invalid_servers:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_servers",
                "message": "Some of the selected servers are not available",
            },
        )

    for uuid in added:
        entry = catalog.get(uuid)
        if not entry or not entry.get("available_for_new", False):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "server_unavailable",
                    "message": "Selected server is not available",
                },
            )

    cost_per_month = sum(int(catalog[uuid].get("discounted_per_month", 0)) for uuid in added)
    total_cost = 0
    charged_months = 0
    if cost_per_month > 0:
        total_cost, charged_months = calculate_prorated_price(
            cost_per_month,
            subscription.end_date,
        )
    else:
        charged_months = get_remaining_months(subscription.end_date)

    added_server_ids = [
        catalog[uuid].get("server_id")
        for uuid in added
        if catalog[uuid].get("server_id") is not None
    ]
    added_server_prices = [
        int(catalog[uuid].get("discounted_per_month", 0)) * charged_months
        for uuid in added
        if catalog[uuid].get("server_id") is not None
    ]

    if total_cost > 0 and getattr(user, "balance_kopeks", 0) < total_cost:
        missing = total_cost - getattr(user, "balance_kopeks", 0)
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "insufficient_funds",
                "message": (
                    "Недостаточно средств на балансе. "
                    f"Не хватает {settings.format_price(missing)}"
                ),
            },
        )

    if total_cost > 0:
        added_names = [catalog[uuid].get("name", uuid) for uuid in added]
        description = (
            f"Добавление серверов: {', '.join(added_names)} на {charged_months} мес"
            if added_names
            else "Изменение списка серверов"
        )

        success = await subtract_user_balance(
            db,
            user,
            total_cost,
            description,
        )
        if not success:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "balance_charge_failed",
                    "message": "Failed to charge user balance",
                },
            )

        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=total_cost,
            description=description,
        )

    if added_server_ids:
        await add_subscription_servers(db, subscription, added_server_ids, added_server_prices)
        await add_user_to_servers(db, added_server_ids)

    removed_server_ids = [
        catalog[uuid].get("server_id")
        for uuid in removed
        if catalog[uuid].get("server_id") is not None
    ]

    if removed_server_ids:
        await remove_subscription_servers(db, subscription.id, removed_server_ids)
        await remove_user_from_servers(db, removed_server_ids)

    ordered_selection = []
    seen_selection = set()
    for uuid in selected_order:
        if uuid in seen_selection:
            continue
        seen_selection.add(uuid)
        ordered_selection.append(uuid)

    subscription.connected_squads = ordered_selection
    subscription.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(subscription)
    try:
        await db.refresh(user)
    except Exception:  # pragma: no cover - defensive refresh safeguard
        pass

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)

    await _with_admin_notification_service(
        lambda service: service.send_subscription_update_notification(
            db,
            user,
            subscription,
            "servers",
            old_servers,
            subscription.connected_squads or [],
            price_paid=max(total_cost, 0),
        )
    )

    return MiniAppSubscriptionUpdateResponse(success=True)


@router.post(
    "/subscription/traffic",
    response_model=MiniAppSubscriptionUpdateResponse,
)
async def update_subscription_traffic_endpoint(
    payload: MiniAppSubscriptionTrafficUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionUpdateResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)
    old_traffic = subscription.traffic_limit_gb

    raw_value = (
        payload.traffic
        if payload.traffic is not None
        else payload.traffic_gb
    )
    if raw_value is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Traffic amount is required"},
        )

    try:
        new_traffic = int(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Invalid traffic amount"},
        ) from None

    if new_traffic < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Traffic amount must be non-negative"},
        )

    if new_traffic == subscription.traffic_limit_gb:
        return MiniAppSubscriptionUpdateResponse(success=True, message="No changes")

    if not settings.is_traffic_selectable():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "traffic_fixed",
                "message": "Traffic cannot be changed for this subscription",
            },
        )

    available_packages: List[int] = []
    for package in settings.get_traffic_packages():
        try:
            gb_value = int(package.get("gb"))
        except (TypeError, ValueError):
            continue
        is_enabled = bool(package.get("enabled", True))
        if package.get("is_active") is False:
            is_enabled = False
        if is_enabled:
            available_packages.append(gb_value)

    if available_packages and new_traffic not in available_packages:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "traffic_unavailable",
                "message": "Selected traffic package is not available",
            },
        )

    months_remaining = get_remaining_months(subscription.end_date)
    period_hint_days = months_remaining * 30 if months_remaining > 0 else None
    traffic_discount = _get_addon_discount_percent_for_user(
        user,
        "traffic",
        period_hint_days,
    )

    old_price_per_month = settings.get_traffic_price(subscription.traffic_limit_gb)
    new_price_per_month = settings.get_traffic_price(new_traffic)

    discounted_old_per_month, _ = apply_percentage_discount(
        old_price_per_month,
        traffic_discount,
    )
    discounted_new_per_month, _ = apply_percentage_discount(
        new_price_per_month,
        traffic_discount,
    )

    price_difference_per_month = discounted_new_per_month - discounted_old_per_month
    total_price_difference = 0

    if price_difference_per_month > 0:
        total_price_difference = price_difference_per_month * months_remaining
        if getattr(user, "balance_kopeks", 0) < total_price_difference:
            missing = total_price_difference - getattr(user, "balance_kopeks", 0)
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "insufficient_funds",
                    "message": (
                        "Недостаточно средств на балансе. "
                        f"Не хватает {settings.format_price(missing)}"
                    ),
                },
            )

        description = (
            "Переключение трафика с "
            f"{subscription.traffic_limit_gb}GB на {new_traffic}GB"
        )

        success = await subtract_user_balance(
            db,
            user,
            total_price_difference,
            description,
        )
        if not success:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "balance_charge_failed",
                    "message": "Failed to charge user balance",
                },
            )

        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=total_price_difference,
            description=f"{description} на {months_remaining} мес",
        )

    subscription.traffic_limit_gb = new_traffic
    subscription.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(subscription)
    try:
        await db.refresh(user)
    except Exception:  # pragma: no cover - defensive refresh safeguard
        pass

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)

    await _with_admin_notification_service(
        lambda service: service.send_subscription_update_notification(
            db,
            user,
            subscription,
            "traffic",
            old_traffic,
            subscription.traffic_limit_gb,
            price_paid=max(total_price_difference, 0),
        )
    )

    return MiniAppSubscriptionUpdateResponse(success=True)


@router.post(
    "/subscription/devices",
    response_model=MiniAppSubscriptionUpdateResponse,
)
async def update_subscription_devices_endpoint(
    payload: MiniAppSubscriptionDevicesUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionUpdateResponse:
    user = await _authorize_miniapp_user(payload.init_data, db)
    subscription = _ensure_paid_subscription(user)
    _validate_subscription_id(payload.subscription_id, subscription)

    raw_value = payload.devices if payload.devices is not None else payload.device_limit
    if raw_value is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Device limit is required"},
        )

    try:
        new_devices = int(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Invalid device limit"},
        ) from None

    if new_devices <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "Device limit must be positive"},
        )

    if settings.MAX_DEVICES_LIMIT > 0 and new_devices > settings.MAX_DEVICES_LIMIT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "devices_limit_exceeded",
                "message": (
                    "Превышен максимальный лимит устройств "
                    f"({settings.MAX_DEVICES_LIMIT})"
                ),
            },
        )

    current_devices = int(subscription.device_limit or settings.DEFAULT_DEVICE_LIMIT or 1)
    old_devices = current_devices

    if new_devices == current_devices:
        return MiniAppSubscriptionUpdateResponse(success=True, message="No changes")

    devices_difference = new_devices - current_devices
    price_to_charge = 0
    charged_months = 0

    if devices_difference > 0:
        current_chargeable = max(0, current_devices - settings.DEFAULT_DEVICE_LIMIT)
        new_chargeable = max(0, new_devices - settings.DEFAULT_DEVICE_LIMIT)
        chargeable_diff = new_chargeable - current_chargeable

        price_per_month = chargeable_diff * settings.PRICE_PER_DEVICE
        months_remaining = get_remaining_months(subscription.end_date)
        period_hint_days = months_remaining * 30 if months_remaining > 0 else None
        devices_discount = _get_addon_discount_percent_for_user(
            user,
            "devices",
            period_hint_days,
        )

        discounted_per_month, _ = apply_percentage_discount(
            price_per_month,
            devices_discount,
        )
        price_to_charge, charged_months = calculate_prorated_price(
            discounted_per_month,
            subscription.end_date,
        )

    if price_to_charge > 0 and getattr(user, "balance_kopeks", 0) < price_to_charge:
        missing = price_to_charge - getattr(user, "balance_kopeks", 0)
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "insufficient_funds",
                "message": (
                    "Недостаточно средств на балансе. "
                    f"Не хватает {settings.format_price(missing)}"
                ),
            },
        )

    if price_to_charge > 0:
        description = (
            "Изменение количества устройств с "
            f"{current_devices} до {new_devices}"
        )
        success = await subtract_user_balance(
            db,
            user,
            price_to_charge,
            description,
        )
        if not success:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "balance_charge_failed",
                    "message": "Failed to charge user balance",
                },
            )

        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price_to_charge,
            description=f"{description} на {charged_months or get_remaining_months(subscription.end_date)} мес",
        )

    subscription.device_limit = new_devices
    subscription.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(subscription)
    try:
        await db.refresh(user)
    except Exception:  # pragma: no cover - defensive refresh safeguard
        pass

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)

    await _with_admin_notification_service(
        lambda service: service.send_subscription_update_notification(
            db,
            user,
            subscription,
            "devices",
            old_devices,
            subscription.device_limit,
            price_paid=max(price_to_charge, 0),
        )
    )

    return MiniAppSubscriptionUpdateResponse(success=True)
