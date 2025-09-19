import hashlib
import logging
from decimal import Decimal
from typing import Optional, Dict, Any

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class MulenPayService:

    def __init__(self):
        self.api_key = settings.MULENPAY_API_KEY
        self.secret_key = settings.MULENPAY_SECRET_KEY
        self.shop_id = settings.MULENPAY_SHOP_ID
        self.base_url = (settings.MULENPAY_API_URL or "https://mulenpay.ru/api").rstrip('/')
        self.language = settings.MULENPAY_LANGUAGE or "ru"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _calculate_signature(self, currency: str, amount_str: str) -> str:
        raw_string = f"{currency}{amount_str}{self.shop_id}{self.secret_key}"
        return hashlib.sha1(raw_string.encode("utf-8")).hexdigest()

    async def create_payment(
        self,
        *,
        amount_kopeks: int,
        description: str,
        uuid: str,
    ) -> Optional[Dict[str, Any]]:

        if not settings.is_mulenpay_enabled():
            logger.error("MulenPay сервис не настроен")
            return None

        currency = "rub"
        amount_decimal = (Decimal(amount_kopeks) / Decimal(100)).quantize(Decimal("0.01"))
        amount_str = format(amount_decimal, ".2f")
        sign = self._calculate_signature(currency, amount_str)

        item = {
            "description": description[:255],
            "quantity": 1,
            "price": float(amount_decimal),
            "vat_code": settings.MULENPAY_VAT_CODE,
            "payment_subject": settings.MULENPAY_PAYMENT_SUBJECT,
            "payment_mode": settings.MULENPAY_PAYMENT_MODE,
            "measurement_unit": settings.MULENPAY_MEASUREMENT_UNIT,
        }

        payload: Dict[str, Any] = {
            "currency": currency,
            "amount": amount_str,
            "uuid": uuid,
            "shopId": int(self.shop_id),
            "description": description[:255],
            "language": self.language,
            "sign": sign,
            "items": [item],
        }

        website_url = settings.get_mulenpay_website_url()
        if website_url:
            payload["website_url"] = website_url

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v2/payments",
                    json=payload,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text_body = await response.text()
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        logger.error(
                            "Ошибка парсинга ответа MulenPay: %s", text_body
                        )
                        return None

                    if response.status == 201 and data.get("success"):
                        logger.info(
                            "Создан MulenPay платеж %s на %s",
                            data.get("id"),
                            amount_str,
                        )
                        return data

                    logger.error(
                        "Ошибка создания MulenPay платежа: статус=%s, ответ=%s",
                        response.status,
                        text_body,
                    )
                    return None
        except Exception as e:
            logger.error(f"Ошибка запроса к MulenPay: {e}")
            return None

    async def get_payment(
        self,
        mulen_payment_id: int,
    ) -> Optional[Dict[str, Any]]:

        if not settings.is_mulenpay_enabled():
            logger.error("MulenPay сервис не настроен")
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/v2/payments/{mulen_payment_id}",
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text_body = await response.text()
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        logger.error(
                            "Ошибка чтения ответа MulenPay при получении платежа %s: %s",
                            mulen_payment_id,
                            text_body,
                        )
                        return None

                    if response.status == 200 and data.get("success"):
                        return data.get("payment")

                    logger.error(
                        "Ошибка получения платежа MulenPay %s: статус=%s, ответ=%s",
                        mulen_payment_id,
                        response.status,
                        text_body,
                    )
                    return None
        except Exception as e:
            logger.error(f"Ошибка запроса статуса MulenPay {mulen_payment_id}: {e}")
            return None
