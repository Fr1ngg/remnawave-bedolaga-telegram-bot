import base64
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from app.config import settings

logger = logging.getLogger(__name__)


class WataService:
    """Интеграция с API платёжного провайдера WATA."""

    def __init__(self) -> None:
        self.base_url = settings.get_wata_base_url()
        self.access_token = settings.WATA_ACCESS_TOKEN
        self.timeout_seconds = max(1, int(settings.WATA_TIMEOUT_SECONDS or 60))
        self._cached_public_key: Optional[RSAPublicKey] = None
        self._cached_public_key_expire_at: Optional[datetime] = None
        self._cached_public_key_pem: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return settings.is_wata_enabled() and bool(self.access_token)

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("Wata service is not configured")
            return None

        url = f"{self.base_url}{endpoint}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=self._build_headers(),
                    json=json_data,
                    params=params,
                ) as response:
                    text = await response.text()
                    if not text:
                        data: Dict[str, Any] = {}
                    else:
                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            data = {}

                    if response.status >= 400:
                        logger.error(
                            "Wata API error %s %s: %s",
                            response.status,
                            endpoint,
                            text,
                        )
                        return None

                    return data
        except aiohttp.ClientError as error:
            logger.error("Wata API request error: %s", error)
            return None
        except Exception as error:  # pragma: no cover - safety
            logger.error("Unexpected Wata API error: %s", error, exc_info=True)
            return None

    @staticmethod
    def _format_amount(amount_kopeks: int) -> str:
        amount = (Decimal(amount_kopeks) / Decimal(100)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        return format(amount, "f")

    async def create_payment_link(
        self,
        *,
        amount_kopeks: int,
        description: str,
        order_id: str,
        type_: Optional[str] = None,
        currency: Optional[str] = None,
        success_redirect_url: Optional[str] = None,
        fail_redirect_url: Optional[str] = None,
        expiration_datetime: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "amount": self._format_amount(amount_kopeks),
            "currency": (currency or settings.WATA_DEFAULT_CURRENCY or "RUB").upper(),
            "description": description,
            "orderId": order_id,
        }

        link_type = (type_ or settings.WATA_LINK_TYPE or "OneTime").strip()
        if link_type:
            payload["type"] = link_type

        if success_redirect_url:
            payload["successRedirectUrl"] = success_redirect_url
        elif settings.WATA_SUCCESS_REDIRECT_URL:
            payload["successRedirectUrl"] = settings.WATA_SUCCESS_REDIRECT_URL

        if fail_redirect_url:
            payload["failRedirectUrl"] = fail_redirect_url
        elif settings.WATA_FAIL_REDIRECT_URL:
            payload["failRedirectUrl"] = settings.WATA_FAIL_REDIRECT_URL

        if expiration_datetime:
            payload["expirationDateTime"] = expiration_datetime.isoformat()

        if metadata:
            payload.update(metadata)

        return await self._request("POST", "/links", json_data=payload)

    async def get_payment_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/links/{link_id}")

    async def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/transactions/{transaction_id}")

    async def _download_public_key(self) -> Optional[RSAPublicKey]:
        response = await self._request("GET", "/public-key")
        if not response:
            return None

        value = response.get("value")
        if not value:
            logger.error("Wata public key response missing value")
            return None

        try:
            pem_bytes = value.encode("utf-8") if isinstance(value, str) else value
            public_key = serialization.load_pem_public_key(pem_bytes)
        except Exception as error:
            logger.error("Failed to load Wata public key: %s", error)
            return None

        if not isinstance(public_key, RSAPublicKey):
            logger.error("Loaded Wata public key is not RSA")
            return None

        self._cached_public_key = public_key
        self._cached_public_key_pem = value if isinstance(value, str) else value.decode("utf-8")
        self._cached_public_key_expire_at = datetime.utcnow() + timedelta(
            seconds=settings.WATA_PUBLIC_KEY_CACHE_SECONDS or 3600
        )
        return public_key

    async def get_public_key(self, *, force: bool = False) -> Optional[RSAPublicKey]:
        if not force and self._cached_public_key is not None:
            if (
                self._cached_public_key_expire_at
                and datetime.utcnow() < self._cached_public_key_expire_at
            ):
                return self._cached_public_key

        return await self._download_public_key()

    async def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        if not signature:
            logger.warning("Пустая подпись Wata webhook")
            return False

        public_key = await self.get_public_key()
        if not public_key:
            logger.warning("Не удалось получить публичный ключ Wata для проверки подписи")
            return False

        try:
            signature_bytes = base64.b64decode(signature)
        except Exception as error:
            logger.warning("Не удалось декодировать подпись Wata: %s", error)
            return False

        try:
            public_key.verify(
                signature_bytes,
                raw_body,
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except Exception as error:
            logger.warning("Ошибка проверки подписи Wata: %s", error)
            return False

    @property
    def cached_public_key_pem(self) -> Optional[str]:
        return self._cached_public_key_pem
