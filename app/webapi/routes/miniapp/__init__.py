from __future__ import annotations

from fastapi import APIRouter

from . import payments, promo, subscription

router = APIRouter()

router.include_router(payments.router)
router.include_router(promo.router)
router.include_router(subscription.router)

_compute_cryptobot_limits = payments._compute_cryptobot_limits
_find_recent_deposit = payments._find_recent_deposit
_resolve_payment_status_entry = payments._resolve_payment_status_entry
_resolve_yookassa_payment_status = payments._resolve_yookassa_payment_status
_resolve_mulenpay_payment_status = payments._resolve_mulenpay_payment_status
_resolve_pal24_payment_status = payments._resolve_pal24_payment_status
_resolve_cryptobot_payment_status = payments._resolve_cryptobot_payment_status
_resolve_stars_payment_status = payments._resolve_stars_payment_status
_resolve_tribute_payment_status = payments._resolve_tribute_payment_status

PaymentService = payments.PaymentService
Bot = payments.Bot
get_wata_payment_by_link_id = payments.get_wata_payment_by_link_id


async def create_payment_link(*args, **kwargs):
    original_resolver = payments._resolve_user_from_init_data
    original_service = payments.PaymentService
    original_bot = payments.Bot
    try:
        payments._resolve_user_from_init_data = globals()["_resolve_user_from_init_data"]
        payments.PaymentService = globals()["PaymentService"]
        payments.Bot = globals()["Bot"]
        return await payments.create_payment_link(*args, **kwargs)
    finally:
        payments._resolve_user_from_init_data = original_resolver
        payments.PaymentService = original_service
        payments.Bot = original_bot


async def get_payment_methods(*args, **kwargs):
    original_resolver = payments._resolve_user_from_init_data
    try:
        payments._resolve_user_from_init_data = globals()["_resolve_user_from_init_data"]
        return await payments.get_payment_methods(*args, **kwargs)
    finally:
        payments._resolve_user_from_init_data = original_resolver


async def get_payment_statuses(*args, **kwargs):
    original_service = payments.PaymentService
    try:
        payments.PaymentService = globals()["PaymentService"]
        return await payments.get_payment_statuses(*args, **kwargs)
    finally:
        payments.PaymentService = original_service


async def _resolve_user_from_init_data(*args, **kwargs):
    return await payments._resolve_user_from_init_data(*args, **kwargs)


async def _resolve_wata_payment_status(*args, **kwargs):
    original_lookup = payments.get_wata_payment_by_link_id
    try:
        payments.get_wata_payment_by_link_id = globals()["get_wata_payment_by_link_id"]
        return await payments._resolve_wata_payment_status(*args, **kwargs)
    finally:
        payments.get_wata_payment_by_link_id = original_lookup

__all__ = [
    "router",
    "PaymentService",
    "Bot",
    "get_wata_payment_by_link_id",
    "create_payment_link",
    "get_payment_methods",
    "get_payment_statuses",
    "_compute_cryptobot_limits",
    "_find_recent_deposit",
    "_resolve_payment_status_entry",
    "_resolve_yookassa_payment_status",
    "_resolve_mulenpay_payment_status",
    "_resolve_wata_payment_status",
    "_resolve_pal24_payment_status",
    "_resolve_cryptobot_payment_status",
    "_resolve_stars_payment_status",
    "_resolve_tribute_payment_status",
    "_resolve_user_from_init_data",
]
