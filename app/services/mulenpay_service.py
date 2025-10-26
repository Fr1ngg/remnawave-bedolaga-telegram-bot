import asyncio
import hashlib
import json
import logging
from typing import Optional, Dict, Any, Tuple

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class MulenPayService:
    """Интеграция с Mulen Pay API."""

    def __init__(self) -> None:
        self.api_key = settings.MULENPAY_API_KEY
        self.shop_id = settings.MULENPAY_SHOP_ID
        self.secret_key = settings.MULENPAY_SECRET_KEY
        self.base_url = settings.MULENPAY_BASE_URL.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=25)
        self._max_retries = 3
        self._retry_delay = 0.5
        self._retryable_statuses = {500, 502, 503, 504}

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.is_mulenpay_enabled()
            and self.api_key
            and self.shop_id
            and self.secret_key
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("MulenPay service is not configured")
            return None

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[BaseException] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=self._timeout) as session:
                    async with session.request(
                        method,
                        url,
                        headers=headers,
                        json=json_data,
                        params=params,
                    ) as response:
                        data, raw_text = await self._deserialize_response(response)

                        if response.status >= 400:
                            logger.error(
                                "MulenPay API error %s %s: %s",
                                response.status,
                                endpoint,
                                raw_text,
                            )
                            if (
                                response.status in self._retryable_statuses
                                and attempt < self._max_retries
                            ):
                                await self._sleep_with_backoff(attempt)
                                continue
                            return None

                        if data is None:
                            if raw_text:
                                logger.warning(
                                    "MulenPay returned unexpected payload for %s: %s",
                                    endpoint,
                                    raw_text,
                                )
                            return None

                        return data
            except asyncio.CancelledError:
                logger.debug("MulenPay request cancelled: %s %s", method, endpoint)
                raise
            except asyncio.TimeoutError as error:
                last_error = error
                logger.warning(
                    "MulenPay request timeout (%s %s) attempt %s/%s",
                    method,
                    endpoint,
                    attempt,
                    self._max_retries,
                )
            except aiohttp.ClientError as error:
                last_error = error
                logger.warning(
                    "MulenPay client error (%s %s) attempt %s/%s: %s",
                    method,
                    endpoint,
                    attempt,
                    self._max_retries,
                    error,
                )
            except Exception as error:  # pragma: no cover - safety
                logger.error("Unexpected MulenPay error: %s", error, exc_info=True)
                return None

            if attempt < self._max_retries:
                await self._sleep_with_backoff(attempt)

        if isinstance(last_error, asyncio.TimeoutError):
            logger.error(
                "MulenPay request timed out after %s attempts: %s %s",
                self._max_retries,
                method,
                endpoint,
            )
        elif last_error is not None:
            logger.error(
                "MulenPay request failed after %s attempts (%s %s): %s",
                self._max_retries,
                method,
                endpoint,
                last_error,
            )

        return None

    async def _sleep_with_backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._retry_delay * attempt)

    async def _deserialize_response(
        self, response: aiohttp.ClientResponse
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        raw_text = await response.text()
        if not raw_text:
            return None, ""

        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower() or not content_type:
            try:
                return json.loads(raw_text), raw_text
            except json.JSONDecodeError as error:
                logger.error(
                    "Failed to decode MulenPay JSON response %s: %s",
                    response.url,
                    error,
                )
                return None, raw_text

        return None, raw_text

    @staticmethod
    def _format_amount(amount_kopeks: int) -> str:
        return f"{amount_kopeks / 100:.2f}"

    def _build_signature(self, currency: str, amount_str: str) -> str:
        raw_string = f"{currency}{amount_str}{self.shop_id}{self.secret_key}".encode()
        return hashlib.sha1(raw_string).hexdigest()

    async def create_payment(
        self,
        *,
        amount_kopeks: int,
        description: str,
        uuid: str,
        items: list,
        language: str = "ru",
        subscribe: Optional[str] = None,
        hold_time: Optional[int] = None,
        website_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("MulenPay service is not configured")
            return None

        amount_str = self._format_amount(amount_kopeks)
        currency = "rub"
        payload = {
            "currency": currency,
            "amount": amount_str,
            "uuid": uuid,
            "shopId": self.shop_id,
            "description": description,
            "items": items,
            "language": language,
            "sign": self._build_signature(currency, amount_str),
        }

        if subscribe:
            payload["subscribe"] = subscribe
        if hold_time is not None:
            payload["holdTime"] = hold_time
        if website_url:
            payload["website_url"] = website_url

        response = await self._request("POST", "/v2/payments", json_data=payload)
        if not response or not response.get("success"):
            logger.error("Failed to create MulenPay payment: %s", response)
            return None

        return response

    async def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/v2/payments/{payment_id}")

    async def list_payments(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        uuid: Optional[str] = None,
        status: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        params = {
            "offset": max(0, offset),
            "limit": max(1, min(limit, 1000)),
        }
        if uuid:
            params["uuid"] = uuid
        if status is not None:
            params["status"] = status
        return await self._request("GET", "/v2/payments", params=params)
