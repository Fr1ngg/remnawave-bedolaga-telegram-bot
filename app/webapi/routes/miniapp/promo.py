from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.discount_offer import get_offer_by_id, mark_offer_claimed
from app.database.crud.promo_offer_template import get_promo_offer_template_by_id
from app.database.crud.server_squad import get_server_squad_by_uuid
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import PromoGroup, PromoOfferTemplate, Subscription, User
from app.services.promo_offer_service import promo_offer_service
from app.services.promocode_service import PromoCodeService
from app.services.remnawave_service import RemnaWaveConfigurationError, RemnaWaveService
from app.utils.telegram_webapp import TelegramWebAppAuthError, parse_webapp_init_data

from ...dependencies import get_db_session
from ...schemas.miniapp import (
    MiniAppConnectedServer,
    MiniAppPromoCode,
    MiniAppPromoCodeActivationRequest,
    MiniAppPromoCodeActivationResponse,
    MiniAppPromoOffer,
    MiniAppPromoOfferClaimRequest,
    MiniAppPromoOfferClaimResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter()

promo_code_service = PromoCodeService()

_TEMPLATE_ID_PATTERN = re.compile(r"promo_template_(?P<template_id>\d+)$")
_OFFER_TYPE_ICONS = {
    "extend_discount": "💎",
    "purchase_discount": "🎯",
    "test_access": "🧪",
}
_EFFECT_TYPE_ICONS = {
    "percent_discount": "🎁",
    "test_access": "🧪",
    "balance_bonus": "💰",
}
_DEFAULT_OFFER_ICON = "🎉"

ActiveOfferContext = Tuple[Any, Optional[int], Optional[datetime]]


async def resolve_connected_servers(
    db: AsyncSession,
    squad_uuids: List[str],
) -> List[MiniAppConnectedServer]:
    if not squad_uuids:
        return []

    resolved: Dict[str, str] = {}
    missing: List[str] = []

    for squad_uuid in squad_uuids:
        if squad_uuid in resolved:
            continue
        server = await get_server_squad_by_uuid(db, squad_uuid)
        if server and server.display_name:
            resolved[squad_uuid] = server.display_name
        else:
            missing.append(squad_uuid)

    if missing:
        try:
            service = RemnaWaveService()
            if service.is_configured:
                squads = await service.get_all_squads()
                for squad in squads:
                    uuid = squad.get("uuid")
                    name = squad.get("name")
                    if uuid in missing and name:
                        resolved[uuid] = name
        except RemnaWaveConfigurationError:
            logger.debug("RemnaWave is not configured; skipping server name enrichment")
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning("Failed to resolve server names from RemnaWave: %s", error)

    connected_servers: List[MiniAppConnectedServer] = []
    for squad_uuid in squad_uuids:
        name = resolved.get(squad_uuid, squad_uuid)
        connected_servers.append(MiniAppConnectedServer(uuid=squad_uuid, name=name))

    return connected_servers


def _extract_template_id(notification_type: Optional[str]) -> Optional[int]:
    if not notification_type:
        return None
    match = _TEMPLATE_ID_PATTERN.search(notification_type)
    if not match:
        return None
    try:
        return int(match.group("template_id"))
    except (TypeError, ValueError):
        return None


def _extract_offer_extra(offer: Any) -> Dict[str, Any]:
    extra = getattr(offer, "extra", None)
    if isinstance(extra, dict):
        return extra
    return {}


def _extract_offer_type(
    offer: Any,
    template: Optional[PromoOfferTemplate],
) -> Optional[str]:
    extra = _extract_offer_extra(offer)
    offer_type = extra.get("offer_type")
    if isinstance(offer_type, str) and offer_type.strip():
        return offer_type.strip()
    if template and isinstance(template.offer_type, str) and template.offer_type.strip():
        return template.offer_type.strip()
    notification_type = getattr(offer, "notification_type", None)
    if isinstance(notification_type, str) and notification_type.strip():
        return notification_type.strip()
    return None


def _extract_offer_test_squad_uuids(offer: Any) -> List[str]:
    extra = _extract_offer_extra(offer)
    squads = extra.get("test_squad_uuids") or extra.get("test_squads")
    if isinstance(squads, list):
        return [str(uuid) for uuid in squads if uuid]
    squad = extra.get("test_squad_uuid")
    if squad:
        return [str(squad)]
    return []


def _normalize_effect_type(effect_type: Optional[str]) -> Optional[str]:
    if not effect_type:
        return None
    normalized = effect_type.replace("-", "_").strip().lower()
    if normalized in {"percent_discount", "test_access", "balance_bonus"}:
        return normalized
    return effect_type


def _format_bonus_label(amount_kopeks: int) -> Optional[str]:
    if amount_kopeks <= 0:
        return None
    return settings.format_price(amount_kopeks)


def _format_offer_message(
    template: Optional[PromoOfferTemplate],
    offer: Any,
    *,
    server_name: Optional[str] = None,
) -> Optional[str]:
    extra = _extract_offer_extra(offer)
    message_text = extra.get("message_text") or extra.get("message")
    if isinstance(message_text, str) and message_text.strip():
        return message_text.strip()
    if template and isinstance(template.message_text, str):
        message = template.message_text.strip()
        if message:
            return message
    if server_name:
        return f"Доступ на сервер {server_name}"
    return None


def extract_promo_discounts(group: PromoGroup) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    try:
        purchase_discount = int(group.purchase_discount_percent or 0)
    except (TypeError, ValueError):
        purchase_discount = 0
    try:
        extend_discount = int(group.extend_discount_percent or 0)
    except (TypeError, ValueError):
        extend_discount = 0

    result["purchase_discount_percent"] = max(0, purchase_discount)
    result["extend_discount_percent"] = max(0, extend_discount)
    return result


async def build_promo_offer_models(
    db: AsyncSession,
    available_offers: List[Any],
    active_offers: Optional[List[ActiveOfferContext]],
    *,
    user: User,
) -> List[MiniAppPromoOffer]:
    promo_offers: List[MiniAppPromoOffer] = []
    template_cache: Dict[int, Optional[PromoOfferTemplate]] = {}

    candidates: List[Any] = [offer for offer in available_offers if offer]
    active_offer_contexts: List[ActiveOfferContext] = []
    if active_offers:
        for offer, discount_override, expires_override in active_offers:
            if not offer:
                continue
            active_offer_contexts.append((offer, discount_override, expires_override))
            candidates.append(offer)

    squad_map: Dict[str, MiniAppConnectedServer] = {}
    if candidates:
        all_uuids: List[str] = []
        for offer in candidates:
            all_uuids.extend(_extract_offer_test_squad_uuids(offer))
        if all_uuids:
            unique = list(dict.fromkeys(all_uuids))
            resolved = await resolve_connected_servers(db, unique)
            squad_map = {server.uuid: server for server in resolved}

    async def get_template(template_id: Optional[int]) -> Optional[PromoOfferTemplate]:
        if not template_id:
            return None
        if template_id not in template_cache:
            template_cache[template_id] = await get_promo_offer_template_by_id(db, template_id)
        return template_cache[template_id]

    def build_test_squads(offer: Any) -> List[MiniAppConnectedServer]:
        test_squads: List[MiniAppConnectedServer] = []
        for uuid in _extract_offer_test_squad_uuids(offer):
            resolved = squad_map.get(uuid)
            if resolved:
                test_squads.append(
                    MiniAppConnectedServer(uuid=resolved.uuid, name=resolved.name)
                )
            else:
                test_squads.append(MiniAppConnectedServer(uuid=uuid, name=uuid))
        return test_squads

    def resolve_title(
        offer: Any,
        template: Optional[PromoOfferTemplate],
        offer_type: Optional[str],
    ) -> Optional[str]:
        extra = _extract_offer_extra(offer)
        if isinstance(extra.get("title"), str) and extra["title"].strip():
            return extra["title"].strip()
        if template and template.name:
            return template.name
        if offer_type:
            return offer_type.replace("_", " ").title()
        return None

    for offer in available_offers:
        template_id = _extract_template_id(getattr(offer, "notification_type", None))
        template = await get_template(template_id)
        effect_type = _normalize_effect_type(getattr(offer, "effect_type", None))
        offer_type = _extract_offer_type(offer, template)
        test_squads = build_test_squads(offer)
        server_name = test_squads[0].name if test_squads else None
        message_text = _format_offer_message(template, offer, server_name=server_name)
        bonus_label = _format_bonus_label(int(getattr(offer, "bonus_amount_kopeks", 0) or 0))
        discount_percent = getattr(offer, "discount_percent", 0)
        try:
            discount_percent = int(discount_percent)
        except (TypeError, ValueError):
            discount_percent = 0

        extra = _extract_offer_extra(offer)
        button_text = None
        if isinstance(extra.get("button_text"), str) and extra["button_text"].strip():
            button_text = extra["button_text"].strip()
        elif template and isinstance(template.button_text, str):
            button_text = template.button_text

        promo_offers.append(
            MiniAppPromoOffer(
                id=int(getattr(offer, "id", 0) or 0),
                status="pending",
                notification_type=getattr(offer, "notification_type", None),
                offer_type=offer_type,
                effect_type=effect_type,
                discount_percent=max(0, discount_percent),
                discount_percent_override=None,
                bonus_amount_label=bonus_label,
                message_text=message_text,
                title=resolve_title(offer, template, offer_type),
                icon=_OFFER_TYPE_ICONS.get(offer_type) or _EFFECT_TYPE_ICONS.get(effect_type) or _DEFAULT_OFFER_ICON,
                expires_at=getattr(offer, "expires_at", None),
                claimed_at=getattr(offer, "claimed_at", None),
                bonus_amount_kopeks=int(getattr(offer, "bonus_amount_kopeks", 0) or 0),
                promo_offer_template_id=template_id,
                test_squads=test_squads,
                extra=extra,
            )
        )

    if active_offer_contexts:
        offer_map: Dict[int, Tuple[Any, Optional[int], Optional[datetime]]] = {}
        for offer, discount_override, expires_override in active_offer_contexts:
            offer_id = getattr(offer, "id", None)
            if not isinstance(offer_id, int):
                continue
            current = offer_map.get(offer_id)
            if current is None:
                offer_map[offer_id] = (offer, discount_override, expires_override)
            else:
                _, _, current_expires = current
                if (current_expires or datetime.min) < (expires_override or datetime.max):
                    offer_map[offer_id] = (offer, discount_override, expires_override)

        for offer_id, (offer, discount_override, expires_override) in offer_map.items():
            for candidate in promo_offers:
                if candidate.id != offer_id:
                    continue
                if discount_override is not None:
                    candidate.discount_percent_override = max(0, discount_override)
                if expires_override is not None:
                    candidate.expires_at = min(candidate.expires_at or expires_override, expires_override)
                candidate.status = "active"
                break

    return promo_offers


async def find_active_test_access_offers(
    db: AsyncSession,
    subscription: Subscription,
) -> List[ActiveOfferContext]:
    if not subscription:
        return []

    query = (
        await promo_offer_service.get_test_access_entries(
            db,
            subscription.id,
            include_expired=False,
        )
    )

    now = datetime.utcnow()
    offer_map: Dict[int, Tuple[Any, Optional[datetime]]] = {}

    for entry in query:
        offer = getattr(entry, "offer", None)
        if not offer:
            continue

        effect_type = _normalize_effect_type(getattr(offer, "effect_type", None))
        if effect_type != "test_access":
            continue

        expires_at = getattr(entry, "expires_at", None)
        if not expires_at or expires_at <= now:
            continue

        offer_id = getattr(offer, "id", None)
        if not isinstance(offer_id, int):
            continue

        current = offer_map.get(offer_id)
        if current is None:
            offer_map[offer_id] = (offer, expires_at)
        else:
            _, current_expiry = current
            if current_expiry is None or (expires_at and expires_at > current_expiry):
                offer_map[offer_id] = (offer, expires_at)

    contexts: List[ActiveOfferContext] = []
    for offer_id, (offer, expires_at) in offer_map.items():
        contexts.append((offer, None, expires_at))

    contexts.sort(key=lambda item: item[2] or now, reverse=True)
    return contexts


@router.post(
    "/promo-codes/activate",
    response_model=MiniAppPromoCodeActivationResponse,
)
async def activate_promo_code(
    payload: MiniAppPromoCodeActivationRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPromoCodeActivationResponse:
    try:
        webapp_data = parse_webapp_init_data(payload.init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": str(error)},
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_user", "message": "Invalid Telegram user payload"},
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_user", "message": "Invalid Telegram user identifier"},
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "user_not_found", "message": "User not found"},
        )

    code = (payload.code or "").strip().upper()
    if not code:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid", "message": "Promo code must not be empty"},
        )

    result = await promo_code_service.activate_promocode(db, user.id, code)
    if result.get("success"):
        promocode_data = result.get("promocode") or {}

        try:
            balance_bonus = int(promocode_data.get("balance_bonus_kopeks") or 0)
        except (TypeError, ValueError):
            balance_bonus = 0

        try:
            subscription_days = int(promocode_data.get("subscription_days") or 0)
        except (TypeError, ValueError):
            subscription_days = 0

        promo_payload = MiniAppPromoCode(
            code=str(promocode_data.get("code") or code),
            type=promocode_data.get("type"),
            balance_bonus_kopeks=balance_bonus,
            subscription_days=subscription_days,
            max_uses=promocode_data.get("max_uses"),
            current_uses=promocode_data.get("current_uses"),
            valid_until=promocode_data.get("valid_until"),
        )

        return MiniAppPromoCodeActivationResponse(
            success=True,
            description=result.get("description"),
            promocode=promo_payload,
        )

    error_code = str(result.get("error") or "generic")
    status_map = {
        "user_not_found": status.HTTP_404_NOT_FOUND,
        "not_found": status.HTTP_404_NOT_FOUND,
        "expired": status.HTTP_410_GONE,
        "used": status.HTTP_409_CONFLICT,
        "already_used_by_user": status.HTTP_409_CONFLICT,
        "server_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    message_map = {
        "invalid": "Promo code must not be empty",
        "not_found": "Promo code not found",
        "expired": "Promo code expired",
        "used": "Promo code already used",
        "already_used_by_user": "Promo code already used by this user",
        "user_not_found": "User not found",
        "server_error": "Failed to activate promo code",
    }

    http_status = status_map.get(error_code, status.HTTP_400_BAD_REQUEST)
    message = message_map.get(error_code, "Unable to activate promo code")

    raise HTTPException(
        http_status,
        detail={"code": error_code, "message": message},
    )


@router.post(
    "/promo-offers/{offer_id}/claim",
    response_model=MiniAppPromoOfferClaimResponse,
)
async def claim_promo_offer(
    offer_id: int,
    payload: MiniAppPromoOfferClaimRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPromoOfferClaimResponse:
    try:
        webapp_data = parse_webapp_init_data(payload.init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": str(error)},
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_user", "message": "Invalid Telegram user payload"},
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_user", "message": "Invalid Telegram user identifier"},
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "user_not_found", "message": "User not found"},
        )

    offer = await get_offer_by_id(db, offer_id)
    if not offer or offer.user_id != user.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "offer_not_found", "message": "Offer not found"},
        )

    now = datetime.utcnow()
    if offer.claimed_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "already_claimed", "message": "Offer already claimed"},
        )

    if not offer.is_active or offer.expires_at <= now:
        offer.is_active = False
        await db.commit()
        raise HTTPException(
            status.HTTP_410_GONE,
            detail={"code": "offer_expired", "message": "Offer expired"},
        )

    effect_type = _normalize_effect_type(getattr(offer, "effect_type", None))

    if effect_type == "test_access":
        success, newly_added, expires_at, error_code = await promo_offer_service.grant_test_access(
            db,
            user,
            offer,
        )

        if not success:
            code = error_code or "claim_failed"
            message_map = {
                "subscription_missing": "Active subscription required",
                "squads_missing": "No squads configured for test access",
                "already_has_access": "Test access already granted",
                "expired": "Offer expired",
            }
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": code, "message": message_map.get(code, "Failed to grant test access")},
            )

        offer.claimed_at = datetime.utcnow()
        offer.is_active = False
        await mark_offer_claimed(db, offer)
        await db.commit()

        return MiniAppPromoOfferClaimResponse(
            success=True,
            effect_type=effect_type,
            newly_added=bool(newly_added),
            expires_at=expires_at,
        )

    if effect_type == "percent_discount":
        success = await promo_offer_service.apply_discount_offer(db, user, offer)
        if not success:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "claim_failed", "message": "Failed to apply discount"},
            )

        offer.claimed_at = datetime.utcnow()
        offer.is_active = False
        await mark_offer_claimed(db, offer)
        await db.commit()

        return MiniAppPromoOfferClaimResponse(
            success=True,
            effect_type=effect_type,
            newly_added=False,
            expires_at=offer.expires_at,
        )

    if effect_type == "balance_bonus":
        success = await promo_offer_service.apply_balance_bonus(db, user, offer)
        if not success:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "claim_failed", "message": "Failed to apply bonus"},
            )

        offer.claimed_at = datetime.utcnow()
        offer.is_active = False
        await mark_offer_claimed(db, offer)
        await db.commit()

        return MiniAppPromoOfferClaimResponse(
            success=True,
            effect_type=effect_type,
            newly_added=False,
            expires_at=offer.expires_at,
        )

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail={"code": "unsupported_effect", "message": "Unsupported offer type"},
    )


__all__ = [
    "router",
    "ActiveOfferContext",
    "build_promo_offer_models",
    "find_active_test_access_offers",
    "extract_promo_discounts",
    "resolve_connected_servers",
]
