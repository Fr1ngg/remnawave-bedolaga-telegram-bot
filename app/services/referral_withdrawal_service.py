from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_withdrawal import (
    close_referral_withdrawal_request,
    create_referral_withdrawal_request,
    get_referral_withdrawal_request_by_id,
    get_referral_withdrawal_requests,
    get_total_requested_amount,
)
from app.database.crud.system_setting import upsert_system_setting
from app.database.models import ReferralWithdrawalRequest, SystemSetting
from app.utils.user_utils import get_user_referral_summary

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReferralWithdrawalSettings:
    enabled: bool
    min_amount_kopeks: int
    prompt_text: str
    success_text: str


class ReferralWithdrawalService:
    ENABLED_KEY = "REFERRAL_WITHDRAWALS_ENABLED"
    MIN_AMOUNT_KEY = "REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS"
    PROMPT_TEXT_KEY = "REFERRAL_WITHDRAWAL_PROMPT_TEXT"
    SUCCESS_TEXT_KEY = "REFERRAL_WITHDRAWAL_SUCCESS_TEXT"

    PROMPT_ALLOWED_FIELDS = {"available", "min_amount"}
    SUCCESS_ALLOWED_FIELDS = {"amount", "available"}

    DEFAULT_PROMPT = (
        "✉️ Укажите реквизиты для вывода и удобный способ связи."
        "\n\nДоступно к выводу: {available}. Минимум: {min_amount}."
    )
    DEFAULT_SUCCESS = (
        "✅ Заявка отправлена! Мы свяжемся с вами, когда обработаем выплату."
    )

    @classmethod
    async def get_settings(cls, db: AsyncSession) -> ReferralWithdrawalSettings:
        result = await db.execute(
            select(SystemSetting.key, SystemSetting.value).where(
                SystemSetting.key.in_(
                    [
                        cls.ENABLED_KEY,
                        cls.MIN_AMOUNT_KEY,
                        cls.PROMPT_TEXT_KEY,
                        cls.SUCCESS_TEXT_KEY,
                    ]
                )
            )
        )
        rows = dict(result.all())

        enabled = cls._parse_bool(
            rows.get(cls.ENABLED_KEY), settings.REFERRAL_WITHDRAWALS_ENABLED
        )
        min_amount = cls._parse_int(
            rows.get(cls.MIN_AMOUNT_KEY), settings.REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS
        )
        prompt_text = rows.get(cls.PROMPT_TEXT_KEY) or settings.REFERRAL_WITHDRAWAL_PROMPT_TEXT
        success_text = rows.get(cls.SUCCESS_TEXT_KEY) or settings.REFERRAL_WITHDRAWAL_SUCCESS_TEXT

        return ReferralWithdrawalSettings(
            enabled=enabled,
            min_amount_kopeks=max(min_amount, 0),
            prompt_text=prompt_text or cls.DEFAULT_PROMPT,
            success_text=success_text or cls.DEFAULT_SUCCESS,
        )

    @classmethod
    async def set_enabled(cls, db: AsyncSession, enabled: bool) -> None:
        await upsert_system_setting(db, cls.ENABLED_KEY, "1" if enabled else "0")
        await db.commit()

    @classmethod
    async def set_min_amount(cls, db: AsyncSession, amount_kopeks: int) -> None:
        amount = max(int(amount_kopeks), 0)
        await upsert_system_setting(db, cls.MIN_AMOUNT_KEY, str(amount))
        await db.commit()

    @classmethod
    async def set_prompt_text(cls, db: AsyncSession, text: str) -> None:
        await upsert_system_setting(db, cls.PROMPT_TEXT_KEY, text)
        await db.commit()

    @classmethod
    async def set_success_text(cls, db: AsyncSession, text: str) -> None:
        await upsert_system_setting(db, cls.SUCCESS_TEXT_KEY, text)
        await db.commit()

    @classmethod
    def _validate_template_fields(cls, template: str, allowed_fields: set[str]) -> bool:
        formatter = string.Formatter()
        try:
            for _literal, field_name, _format_spec, _conversion in formatter.parse(
                template
            ):
                if field_name and field_name not in allowed_fields:
                    return False

            template.format(**{field: "" for field in allowed_fields})
        except (KeyError, ValueError):
            return False

        return True

    @classmethod
    def validate_prompt_template(cls, template: str) -> bool:
        return cls._validate_template_fields(template, cls.PROMPT_ALLOWED_FIELDS)

    @classmethod
    def validate_success_template(cls, template: str) -> bool:
        return cls._validate_template_fields(template, cls.SUCCESS_ALLOWED_FIELDS)

    @classmethod
    def _safe_format_template(
        cls,
        template: str,
        values: dict[str, str],
        allowed_fields: set[str],
        fallback_template: str,
    ) -> str:
        if not cls._validate_template_fields(template, allowed_fields):
            logger.warning("Неверные плейсхолдеры в шаблоне вывода: %s", template)
            template = fallback_template

        try:
            return template.format(**values)
        except Exception:
            logger.exception("Ошибка форматирования шаблона вывода: %s", template)
            return fallback_template.format(**values)

    @classmethod
    def format_prompt_text(
        cls,
        template: str,
        values: dict[str, str],
        fallback_template: str,
    ) -> str:
        return cls._safe_format_template(
            template, values, cls.PROMPT_ALLOWED_FIELDS, fallback_template
        )

    @classmethod
    def format_success_text(
        cls,
        template: str,
        values: dict[str, str],
        fallback_template: str,
    ) -> str:
        return cls._safe_format_template(
            template, values, cls.SUCCESS_ALLOWED_FIELDS, fallback_template
        )

    @classmethod
    async def get_available_amount(cls, db: AsyncSession, user_id: int) -> int:
        summary = await get_user_referral_summary(db, user_id)
        total_earned = summary.get("total_earned_kopeks", 0)
        already_requested = await get_total_requested_amount(db, user_id)
        available = max(total_earned - already_requested, 0)
        logger.debug(
            "Расчёт доступного реферального дохода: total=%s, requested=%s, available=%s",
            total_earned,
            already_requested,
            available,
        )
        return available

    @classmethod
    async def create_request(
        cls, db: AsyncSession, user_id: int, requisites: str
    ) -> Optional[ReferralWithdrawalRequest]:
        settings_obj = await cls.get_settings(db)
        available = await cls.get_available_amount(db, user_id)

        if not settings_obj.enabled:
            logger.info("Попытка создать заявку на вывод при отключенной функции")
            return None

        if available < settings_obj.min_amount_kopeks:
            logger.info(
                "Недостаточно средств для вывода: user=%s, available=%s, min=%s",
                user_id,
                available,
                settings_obj.min_amount_kopeks,
            )
            return None

        return await create_referral_withdrawal_request(
            db=db,
            user_id=user_id,
            amount_kopeks=available,
            requisites=requisites,
        )

    @classmethod
    async def list_requests(
        cls, db: AsyncSession, status: Optional[str] = None, limit: int = 50
    ):
        return await get_referral_withdrawal_requests(db, status=status, limit=limit)

    @classmethod
    async def get_request(
        cls, db: AsyncSession, request_id: int
    ) -> Optional[ReferralWithdrawalRequest]:
        return await get_referral_withdrawal_request_by_id(db, request_id)

    @classmethod
    async def close_request(
        cls, db: AsyncSession, request_id: int, closed_by_id: Optional[int]
    ) -> Optional[ReferralWithdrawalRequest]:
        request = await get_referral_withdrawal_request_by_id(db, request_id)
        if not request:
            return None
        if request.status == "closed":
            return request
        return await close_referral_withdrawal_request(
            db=db, request=request, closed_by_id=closed_by_id
        )

    @staticmethod
    def _parse_bool(value: Optional[str], default: bool) -> bool:
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _parse_int(value: Optional[str], default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def parse_amount_to_kopeks(raw: str) -> Optional[int]:
        try:
            cleaned = raw.replace(" ", "").replace(",", ".")
            amount = float(cleaned)
            if amount <= 0:
                return None
            return int(amount * 100)
        except Exception:
            return None

