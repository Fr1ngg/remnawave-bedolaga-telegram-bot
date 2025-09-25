import asyncio
import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

import aiohttp
from aiohttp import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings

if TYPE_CHECKING:
    from app.database.models import Subscription
    from app.external.remnawave_api import RemnaWaveAPI, RemnaWaveUser


logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

GOOGLE_TOKEN_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
GOOGLE_TOKEN_DEFAULT_URI = "https://oauth2.googleapis.com/token"
TOKEN_EXPIRY_SAFETY_MARGIN = 60
HTTP_TIMEOUT_SECONDS = 30

CLIENT_TYPE_EXTENSIONS = {
    "clash": ("yaml", "text/yaml"),
    "stash": ("yaml", "text/yaml"),
    "mihomo": ("yaml", "text/yaml"),
    "singbox": ("json", "application/json"),
    "singbox-legacy": ("json", "application/json"),
    "json": ("json", "application/json"),
    "v2ray-json": ("json", "application/json"),
}


class GoogleDriveService:
    def __init__(self) -> None:
        if not settings.is_gdrive_enabled():
            raise RuntimeError("Google Drive integration is not enabled")

        self._service_account: Optional[Dict[str, Any]] = None
        self._access_token: Optional[str] = None
        self._access_token_expires_at: float = 0
        self._token_lock = asyncio.Lock()

    def _load_service_account(self) -> Dict[str, Any]:
        if self._service_account is not None:
            return self._service_account

        info = settings.GDRIVE_SERVICE_ACCOUNT_INFO
        service_account_data: Optional[Dict[str, Any]] = None

        if info:
            try:
                service_account_data = json.loads(info)
                logger.debug("Loaded Google Drive credentials from inline JSON info")
            except json.JSONDecodeError as exc:
                logger.error(f"Failed to decode GDRIVE_SERVICE_ACCOUNT_INFO: {exc}")

        if service_account_data is None and settings.GDRIVE_SERVICE_ACCOUNT_FILE:
            path = Path(settings.GDRIVE_SERVICE_ACCOUNT_FILE)
            if not path.exists():
                raise FileNotFoundError(
                    f"Google Drive service account file not found: {path}"
                )

            with path.open("r", encoding="utf-8") as fp:
                service_account_data = json.load(fp)
            logger.debug(f"Loaded Google Drive credentials from file: {path}")

        if service_account_data is None:
            raise RuntimeError("Google Drive credentials are not configured")

        missing_fields = [
            field
            for field in ("client_email", "private_key")
            if not service_account_data.get(field)
        ]

        if missing_fields:
            raise RuntimeError(
                "Google Drive credentials are missing required fields: "
                + ", ".join(missing_fields)
            )

        self._service_account = service_account_data
        return self._service_account

    @staticmethod
    def _base64url_encode(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    def _build_jwt_assertion(self, service_account_data: Dict[str, Any]) -> str:
        header = {"alg": "RS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "iss": service_account_data["client_email"],
            "scope": " ".join(SCOPES),
            "aud": service_account_data.get("token_uri") or GOOGLE_TOKEN_DEFAULT_URI,
            "iat": now,
            "exp": now + 3600,
        }

        signing_input = b".".join(
            self._base64url_encode(json.dumps(part, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            for part in (header, payload)
        )

        private_key = service_account_data["private_key"].encode("utf-8")
        key = serialization.load_pem_private_key(private_key, password=None)
        signature = key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        return b".".join([signing_input, self._base64url_encode(signature)]).decode("utf-8")

    async def _get_access_token(self) -> str:
        async with self._token_lock:
            now = time.time()
            if (
                self._access_token
                and now < self._access_token_expires_at - TOKEN_EXPIRY_SAFETY_MARGIN
            ):
                return self._access_token

            service_account_data = self._load_service_account()
            assertion = self._build_jwt_assertion(service_account_data)
            token_uri = service_account_data.get("token_uri") or GOOGLE_TOKEN_DEFAULT_URI

            data = {
                "grant_type": GOOGLE_TOKEN_GRANT_TYPE,
                "assertion": assertion,
            }

            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    async with session.post(token_uri, data=data) as response:
                        body = await response.text()
                        if response.status >= 400:
                            logger.error(
                                "Failed to obtain Google Drive access token: %s %s",
                                response.status,
                                body,
                            )
                            raise RuntimeError(
                                "Unable to obtain Google Drive access token"
                            )

                        payload = json.loads(body)
                except ClientError as exc:
                    logger.error(f"Error requesting Google Drive access token: {exc}")
                    raise RuntimeError("Unable to obtain Google Drive access token") from exc

            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in", 3600)

            if not access_token:
                raise RuntimeError("Google Drive token response did not include access token")

            self._access_token = access_token
            self._access_token_expires_at = now + int(expires_in)
            return access_token

    @staticmethod
    def _build_auth_headers(access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _resolve_format(client_type: str) -> Tuple[str, str]:
        return CLIENT_TYPE_EXTENSIONS.get(client_type, ("txt", "text/plain"))

    async def publish_subscription(
        self,
        *,
        existing_file_id: Optional[str],
        short_uuid: str,
        content: str,
        client_type: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not content:
            logger.warning("Received empty subscription content for Google Drive upload")
            return existing_file_id, None

        self._load_service_account()
        access_token = await self._get_access_token()
        headers = self._build_auth_headers(access_token)
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

        extension, mime_type = self._resolve_format(client_type)
        file_name = settings.format_gdrive_file_name(short_uuid, client_type, extension)
        data_bytes = content.encode("utf-8")

        async with aiohttp.ClientSession(timeout=timeout) as session:
            file_id: Optional[str] = None

            try:
                if existing_file_id:
                    file_id = await self._update_file(
                        session,
                        headers,
                        existing_file_id,
                        data_bytes,
                        mime_type,
                    )

                if not file_id:
                    file_id = await self._create_file(
                        session,
                        headers,
                        file_name,
                        data_bytes,
                        mime_type,
                    )

                if settings.GDRIVE_MAKE_PUBLIC:
                    await self._ensure_public_permission(session, headers, file_id)

                file_metadata = await self._get_file_metadata(session, headers, file_id)

            except Exception as exc:
                logger.error(f"Failed to publish subscription to Google Drive: {exc}")
                return existing_file_id, None

        share_link = settings.format_gdrive_share_link(
            file_id,
            default=file_metadata.get("webContentLink") or file_metadata.get("webViewLink"),
        )

        if not share_link:
            share_link = file_metadata.get("webContentLink") or file_metadata.get("webViewLink")

        return file_id, share_link

    async def _update_file(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str],
        file_id: str,
        content: bytes,
        mime_type: str,
    ) -> Optional[str]:
        update_headers = {**headers, "Content-Type": mime_type}
        url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"

        async with session.patch(url, data=content, headers=update_headers) as response:
            if response.status == 404:
                logger.info(
                    "Google Drive subscription file %s not found. Creating a new one instead.",
                    file_id,
                )
                return None

            if response.status >= 400:
                body = await response.text()
                raise RuntimeError(
                    f"Failed to update Google Drive subscription file {file_id}: "
                    f"{response.status} {body}"
                )

        logger.info(f"Updating Google Drive subscription file {file_id}")
        return file_id

    async def _create_file(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str],
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> str:
        boundary = uuid.uuid4().hex
        metadata: Dict[str, Any] = {
            "name": file_name,
            "mimeType": mime_type,
        }

        if settings.GDRIVE_SUBSCRIPTIONS_FOLDER_ID:
            metadata["parents"] = [settings.GDRIVE_SUBSCRIPTIONS_FOLDER_ID]

        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")

        create_headers = headers.copy()
        create_headers["Content-Type"] = f"multipart/related; boundary={boundary}"

        url = (
            "https://www.googleapis.com/upload/drive/v3/files"
            "?uploadType=multipart&fields=id,webViewLink,webContentLink"
        )

        logger.info(f"Creating new Google Drive subscription file '{file_name}'")

        async with session.post(url, data=body, headers=create_headers) as response:
            payload_text = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"Failed to create Google Drive subscription file: "
                    f"{response.status} {payload_text}"
                )

            payload = json.loads(payload_text)

        file_id = payload.get("id")

        if not file_id:
            raise RuntimeError(
                "Google Drive did not return file ID for the created subscription"
            )

        return file_id

    async def _get_file_metadata(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str],
        file_id: str,
    ) -> Dict[str, Any]:
        url = (
            f"https://www.googleapis.com/drive/v3/files/{file_id}"
            "?fields=id,webViewLink,webContentLink"
        )

        async with session.get(url, headers=headers) as response:
            payload_text = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"Failed to fetch Google Drive metadata for {file_id}: "
                    f"{response.status} {payload_text}"
                )

            return json.loads(payload_text)

    async def _ensure_public_permission(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str],
        file_id: str,
    ) -> None:
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"

        payload = {
            "role": "reader",
            "type": "anyone",
        }

        async with session.post(url, json=payload, headers=headers) as response:
            if response.status in {200, 204}:
                logger.debug(
                    f"Granted public read permission for Google Drive file {file_id}"
                )
                return

            if response.status == 403:
                logger.warning(
                    "Insufficient permissions to make Google Drive file public. "
                    "The link will still be returned but may require authentication."
                )
                return

            body = await response.text()
            raise RuntimeError(
                f"Failed to update Google Drive permissions for {file_id}: "
                f"{response.status} {body}"
            )


async def sync_subscription_to_gdrive(
    subscription: "Subscription",
    api: "RemnaWaveAPI",
    remnawave_user: "RemnaWaveUser",
) -> None:
    if not settings.is_gdrive_enabled():
        return

    short_uuid = getattr(remnawave_user, "short_uuid", None)
    if not short_uuid:
        logger.warning("Google Drive sync skipped: missing short UUID for subscription")
        return

    client_type = settings.get_gdrive_client_type()

    try:
        subscription_content = await api.get_subscription_by_client_type(short_uuid, client_type)
    except Exception as exc:
        logger.error(
            "Не удалось получить подписку %s для выгрузки в Google Drive: %s",
            short_uuid,
            exc,
        )
        return

    try:
        drive_service = GoogleDriveService()
    except Exception as exc:
        logger.error(f"Не удалось инициализировать Google Drive сервис: {exc}")
        return

    existing_file_id = getattr(subscription, "gdrive_file_id", None)

    file_id, link = await drive_service.publish_subscription(
        existing_file_id=existing_file_id,
        short_uuid=short_uuid,
        content=subscription_content,
        client_type=client_type,
    )

    if file_id and getattr(subscription, "gdrive_file_id", None) != file_id:
        subscription.gdrive_file_id = file_id
        logger.info(f"📤 Google Drive файл обновлен: {file_id}")

    if link:
        if getattr(subscription, "gdrive_link", None) != link:
            subscription.gdrive_link = link
            logger.info(f"🔗 Google Drive ссылка подписки обновлена: {link}")
    elif not getattr(subscription, "gdrive_link", None):
        logger.warning("Google Drive не вернул ссылку для подписки %s", short_uuid)
