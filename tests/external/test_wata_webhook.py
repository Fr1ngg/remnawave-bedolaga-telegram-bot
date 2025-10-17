"""Unit tests for the WATA webhook handler."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
from typing import Optional

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.external.wata_webhook import WataWebhookHandler  # noqa: E402


class DummyPaymentService:
    async def process_wata_webhook(self, *args, **kwargs):  # pragma: no cover - not used in tests
        return True


class StubPublicKeyProvider:
    def __init__(self, public_key_pem: Optional[str]) -> None:
        self.public_key_pem = public_key_pem

    async def get_public_key(self) -> Optional[str]:
        return self.public_key_pem


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_verify_signature_success() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    payload = "{\"status\": \"Paid\"}"
    signature = base64.b64encode(
        private_key.sign(
            payload.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA512(),
        )
    ).decode("utf-8")

    handler = WataWebhookHandler(
        DummyPaymentService(),
        public_key_provider=StubPublicKeyProvider(public_key),
    )

    assert await handler._verify_signature(payload, signature) is True


@pytest.mark.anyio("asyncio")
async def test_verify_signature_fails_with_invalid_signature() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    payload = "{\"status\": \"Paid\"}"
    bad_signature = base64.b64encode(b"not-a-signature").decode("utf-8")

    handler = WataWebhookHandler(
        DummyPaymentService(),
        public_key_provider=StubPublicKeyProvider(public_key),
    )

    assert await handler._verify_signature(payload, bad_signature) is False


@pytest.mark.anyio("asyncio")
async def test_verify_signature_fails_without_public_key() -> None:
    handler = WataWebhookHandler(
        DummyPaymentService(),
        public_key_provider=StubPublicKeyProvider(None),
    )

    assert await handler._verify_signature("{}", "signature") is False
