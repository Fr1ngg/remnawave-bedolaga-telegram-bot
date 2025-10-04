from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PromoOfferTemplate


DEFAULT_TEMPLATES: tuple[dict, ...] = (
    {
        "offer_type": "test_access",
        "name": "Тестовые сервера",
        "message_text": (
            "🔥 <b>Испытайте новые сервера</b>\n\n"
            "Активируйте предложение и получите временный доступ к дополнительным сквадам на {test_duration_hours} ч.\n"
            "Предложение действительно {valid_hours} ч."
        ),
        "button_text": "🚀 Попробовать серверы",
        "valid_hours": 24,
        "discount_percent": 0,
        "bonus_amount_kopeks": 0,
        "test_duration_hours": 24,
        "test_squad_uuids": [],
    },
    {
        "offer_type": "extend_discount",
        "name": "Скидка на продление",
        "message_text": (
            "💎 <b>Экономия {discount_percent}% при продлении</b>\n\n"
            "Активируйте предложение, и скидка применится к следующему продлению автоматически.\n"
            "Срок действия предложения — {valid_hours} ч."
        ),
        "button_text": "🎁 Получить скидку",
        "valid_hours": 24,
        "discount_percent": 20,
        "bonus_amount_kopeks": 0,
        "test_duration_hours": None,
        "test_squad_uuids": [],
    },
    {
        "offer_type": "purchase_discount",
        "name": "Скидка на покупку",
        "message_text": (
            "🎯 <b>Вернитесь со скидкой {discount_percent}%</b>\n\n"
            "После активации скидка применится при оформлении новой подписки.\n"
            "Предложение действует {valid_hours} ч."
        ),
        "button_text": "🎁 Забрать скидку",
        "valid_hours": 48,
        "discount_percent": 25,
        "bonus_amount_kopeks": 0,
        "test_duration_hours": None,
        "test_squad_uuids": [],
    },
)


def _format_template_fields(payload: dict) -> dict:
    data = dict(payload)
    data.setdefault("valid_hours", 24)
    data.setdefault("discount_percent", 0)
    data.setdefault("bonus_amount_kopeks", 0)
    data.setdefault("test_duration_hours", None)
    data.setdefault("test_squad_uuids", [])
    return data


async def ensure_default_templates(db: AsyncSession, *, created_by: Optional[int] = None) -> List[PromoOfferTemplate]:
    templates: List[PromoOfferTemplate] = []

    for template_data in DEFAULT_TEMPLATES:
        result = await db.execute(
            select(PromoOfferTemplate).where(PromoOfferTemplate.offer_type == template_data["offer_type"])
        )
        existing = result.scalars().first()
        if existing:
            templates.append(existing)
            continue

        payload = _format_template_fields(template_data)
        template = PromoOfferTemplate(
            name=payload["name"],
            offer_type=payload["offer_type"],
            message_text=payload["message_text"],
            button_text=payload["button_text"],
            valid_hours=payload["valid_hours"],
            discount_percent=payload["discount_percent"],
            bonus_amount_kopeks=payload["bonus_amount_kopeks"],
            test_duration_hours=payload["test_duration_hours"],
            test_squad_uuids=payload["test_squad_uuids"],
            is_active=True,
            created_by=created_by,
        )
        db.add(template)
        await db.flush()
        templates.append(template)

    await db.commit()

    return templates


async def list_promo_offer_templates(db: AsyncSession) -> List[PromoOfferTemplate]:
    result = await db.execute(
        select(PromoOfferTemplate).order_by(PromoOfferTemplate.offer_type, PromoOfferTemplate.id)
    )
    return result.scalars().all()


async def get_promo_offer_template_by_id(db: AsyncSession, template_id: int) -> Optional[PromoOfferTemplate]:
    result = await db.execute(
        select(PromoOfferTemplate).where(PromoOfferTemplate.id == template_id)
    )
    return result.scalar_one_or_none()


async def get_promo_offer_template_by_type(db: AsyncSession, offer_type: str) -> Optional[PromoOfferTemplate]:
    result = await db.execute(
        select(PromoOfferTemplate).where(PromoOfferTemplate.offer_type == offer_type)
    )
    return result.scalar_one_or_none()


async def update_promo_offer_template(
    db: AsyncSession,
    template: PromoOfferTemplate,
    *,
    name: Optional[str] = None,
    message_text: Optional[str] = None,
    button_text: Optional[str] = None,
    valid_hours: Optional[int] = None,
    discount_percent: Optional[int] = None,
    bonus_amount_kopeks: Optional[int] = None,
    test_duration_hours: Optional[int] = None,
    test_squad_uuids: Optional[Iterable[str]] = None,
    is_active: Optional[bool] = None,
) -> PromoOfferTemplate:
    if name is not None:
        template.name = name
    if message_text is not None:
        template.message_text = message_text
    if button_text is not None:
        template.button_text = button_text
    if valid_hours is not None:
        template.valid_hours = valid_hours
    if discount_percent is not None:
        template.discount_percent = discount_percent
    if bonus_amount_kopeks is not None:
        template.bonus_amount_kopeks = bonus_amount_kopeks
    if test_duration_hours is not None or template.offer_type == "test_access":
        template.test_duration_hours = test_duration_hours
    if test_squad_uuids is not None:
        template.test_squad_uuids = list(test_squad_uuids)
    if is_active is not None:
        template.is_active = is_active

    template.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(template)
    return template
