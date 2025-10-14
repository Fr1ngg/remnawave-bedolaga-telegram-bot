"""High-level integration with the WATA payments API."""

from __future__ import annotations

import base64
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import ClientTimeout
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from app.config import settings

logger = logging.getLogger(__name__)


class WataService:
    """A small wrapper around the WATA REST API."""

    def __init__(self) -> None:
        self.base_url = settings.WATA_BASE_URL.rstrip("/")
        self.access_token = settings.WATA_ACCESS_TOKEN
        self.timeout = max(1, int(settings.WATA_REQUEST_TIMEOUT))
        self._cached_public_key: Optional[bytes] = None
        self._public_key_cached_at: Optional[float] = None

    @property
    def is_configured(self) -> bool:
        return settings.is_wata_enabled()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("WATA service is not configured")
            return None

        endpoint_clean = endpoint.lstrip("/")
        url = f"{self.base_url}/{endpoint_clean}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            timeout = ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_data,
                    params=params,
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        raw_text = await response.text()
                        logger.error(
                            "WATA API unexpected response %s %s: %s",
                            response.status,
                            endpoint,
                            raw_text,
                        )
                        return None

                    data = await response.json()
                    if response.status >= 400:
                        logger.error(
                            "WATA API error %s %s: %s",
                            response.status,
                            endpoint,
                            data,
                        )
                        return None

                    return data
        except aiohttp.ClientError as error:
            logger.error("WATA API request error: %s", error)
            return None
        except Exception as error:  # pragma: no cover - unexpected safety
            logger.error("Unexpected WATA API error: %s", error, exc_info=True)
            return None

    @staticmethod
    def _format_amount(amount_kopeks: int) -> str:
        return f"{Decimal(amount_kopeks) / Decimal(100):.2f}"

    @staticmethod
    def _normalize_optional(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    async def create_payment_link(
        self,
        *,
        amount_kopeks: int,
        currency: str,
        description: str,
        order_id: str,
        link_type: str,
        success_redirect_url: Optional[str] = None,
        fail_redirect_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "type": link_type,
            "amount": self._format_amount(amount_kopeks),
            "currency": currency,
            "description": description,
            "orderId": order_id,
        }

        success_url = self._normalize_optional(success_redirect_url)
        fail_url = self._normalize_optional(fail_redirect_url)

        if success_url:
            payload["successRedirectUrl"] = success_url
        if fail_url:
            payload["failRedirectUrl"] = fail_url

        return await self._request("POST", "/links", json_data=payload)

    async def get_payment_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/links/{link_id}")

    async def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/transactions/{transaction_id}")

    async def find_transactions(
        self,
        *,
        order_id: Optional[str] = None,
        payment_link_id: Optional[str] = None,
        max_results: int = 1,
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "maxResultCount": max(1, min(max_results, 1000)),
        }
        if order_id:
            params["orderId"] = order_id
        if payment_link_id:
            params["paymentLinkId"] = payment_link_id

        return await self._request("GET", "/transactions/", params=params)

    async def _fetch_public_key(self) -> Optional[bytes]:
        response = await self._request("GET", "/public-key")
        if not response:
            return None

        value = response.get("value")
        if not isinstance(value, str):
            return None

        return value.encode("utf-8")

    async def _get_public_key(self) -> Optional[bytes]:
        cache_ttl = max(60, int(settings.WATA_PUBLIC_KEY_CACHE_TTL_SECONDS))
        now = time.time()

        if (
            self._cached_public_key is not None
            and self._public_key_cached_at is not None
            and now - self._public_key_cached_at < cache_ttl
        ):
            return self._cached_public_key

        key_bytes = await self._fetch_public_key()
        if key_bytes:
            self._cached_public_key = key_bytes
            self._public_key_cached_at = now
        return key_bytes

    async def verify_webhook_signature(
        self,
        raw_body: bytes | str,
        signature: Optional[str],
    ) -> bool:
        if not signature:
            logger.warning("WATA webhook without signature header")
            return False

        payload_bytes = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")

        try:
            signature_bytes = base64.b64decode(signature)
        except Exception:
            logger.warning("WATA webhook signature is not valid base64")
            return False

        public_key_bytes = await self._get_public_key()
        if not public_key_bytes:
            logger.error("Unable to retrieve WATA public key for webhook verification")
            return False

        try:
            public_key = load_pem_public_key(public_key_bytes)
            public_key.verify(
                signature_bytes,
                payload_bytes,
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except (ValueError, InvalidSignature) as error:
            logger.warning("WATA webhook signature verification failed: %s", error)
            return False
        except Exception as error:  # pragma: no cover - defensive
            logger.error("Unexpected error verifying WATA signature: %s", error, exc_info=True)
            return False

    @staticmethod
    def parse_amount_to_kopeks(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return int((decimal_value * Decimal("100")).quantize(Decimal("1")))
