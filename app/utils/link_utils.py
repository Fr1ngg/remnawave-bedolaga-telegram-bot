"""Utility helpers for working with external links."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_GOOGLE_DRIVE_HOSTS = {
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
}


def _is_google_drive_host(host: str) -> bool:
    host = host.lower()
    for candidate in _GOOGLE_DRIVE_HOSTS:
        if host == candidate or host.endswith(f".{candidate}"):
            return True
    return False


def _extract_drive_file_id(parsed_url) -> Optional[str]:
    """Extract a Google Drive file identifier from the parsed URL."""
    path_match = re.search(r"/d/([^/]+)", parsed_url.path)
    if path_match:
        return path_match.group(1)

    query_params = parse_qs(parsed_url.query)
    for key in ("id", "file_id", "fileid", "resid"):
        values = query_params.get(key)
        if values:
            return values[0]

    # Handle cases like /uc/<id> without query params
    uc_match = re.search(r"/uc/([^/?#]+)", parsed_url.path)
    if uc_match:
        return uc_match.group(1)

    return None


def normalize_subscription_url(url: Optional[str]) -> str:
    """Normalize subscription links and add support for Google Drive sources.

    Converts shared Google Drive links to a direct download URL that can be
    consumed by VPN clients for automatic configuration updates.
    """

    if not url:
        return ""

    cleaned_url = url.strip()
    if not cleaned_url:
        return ""

    parsed = urlparse(cleaned_url)

    if not parsed.scheme:
        # Assume HTTPS for bare hosts
        parsed = urlparse(f"https://{cleaned_url}")

    if not _is_google_drive_host(parsed.netloc):
        return cleaned_url

    file_id = _extract_drive_file_id(parsed)
    if not file_id:
        return cleaned_url

    sanitized_id = file_id.strip().strip('/').split('?')[0].split('#')[0]
    sanitized_id = re.sub(r"[^0-9A-Za-z_-]", "", sanitized_id)

    if not sanitized_id:
        return cleaned_url

    return f"https://drive.google.com/uc?export=download&id={sanitized_id}"
