"""Утилиты безопасности и генерации ключей."""
from __future__ import annotations

import hashlib
import secrets
from typing import Literal


HashAlgorithm = Literal["sha256", "sha384", "sha512"]


def hash_api_token(token: str, algorithm: HashAlgorithm = "sha256") -> str:
    """Возвращает хеш токена в формате hex."""
    normalized = (algorithm or "sha256").lower()
    if normalized not in {"sha256", "sha384", "sha512"}:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    digest = getattr(hashlib, normalized)
    return digest(token.encode("utf-8")).hexdigest()


def generate_api_token(length: int = 48) -> str:
    """Генерирует криптографически стойкий токен."""
    length = max(24, min(length, 128))
    return secrets.token_urlsafe(length)


__all__ = ["hash_api_token", "generate_api_token", "HashAlgorithm"]
