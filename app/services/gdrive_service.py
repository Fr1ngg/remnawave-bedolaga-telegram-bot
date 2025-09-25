import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaInMemoryUpload

    _GOOGLE_SDK_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - executed only when dependency missing
    service_account = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]
    MediaInMemoryUpload = None  # type: ignore[assignment]

    class HttpError(Exception):
        """Fallback HttpError used when Google SDK is unavailable."""

        def __init__(self, *args, status: Optional[int] = None, **kwargs) -> None:
            super().__init__(*args)
            self.resp = type("_Resp", (), {"status": status})()

    _GOOGLE_SDK_AVAILABLE = False

from app.config import settings

if TYPE_CHECKING:
    from app.database.models import Subscription
    from app.external.remnawave_api import RemnaWaveAPI, RemnaWaveUser


logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

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

        if not _GOOGLE_SDK_AVAILABLE:
            raise RuntimeError(
                "Google Drive integration requires optional dependencies: "
                "google-api-python-client, google-auth, google-auth-httplib2"
            )

        self._credentials = None
        self._service = None

    def _load_credentials(self):
        if self._credentials is not None:
            return self._credentials

        info = settings.GDRIVE_SERVICE_ACCOUNT_INFO
        credentials = None

        if info:
            try:
                data = json.loads(info)
                credentials = service_account.Credentials.from_service_account_info(
                    data, scopes=SCOPES
                )
                logger.debug("Loaded Google Drive credentials from inline JSON info")
            except json.JSONDecodeError as exc:
                logger.error(f"Failed to decode GDRIVE_SERVICE_ACCOUNT_INFO: {exc}")

        if credentials is None and settings.GDRIVE_SERVICE_ACCOUNT_FILE:
            path = Path(settings.GDRIVE_SERVICE_ACCOUNT_FILE)
            if not path.exists():
                raise FileNotFoundError(
                    f"Google Drive service account file not found: {path}"
                )

            credentials = service_account.Credentials.from_service_account_file(
                str(path), scopes=SCOPES
            )
            logger.debug(f"Loaded Google Drive credentials from file: {path}")

        if credentials is None:
            raise RuntimeError("Google Drive credentials are not configured")

        self._credentials = credentials
        return self._credentials

    def _get_service(self):
        if self._service is None:
            credentials = self._load_credentials()
            self._service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )
        return self._service

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

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._publish_subscription_sync,
            existing_file_id,
            short_uuid,
            content,
            client_type,
        )

    def _publish_subscription_sync(
        self,
        existing_file_id: Optional[str],
        short_uuid: str,
        content: str,
        client_type: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        service = self._get_service()
        extension, mime_type = self._resolve_format(client_type)
        file_name = settings.format_gdrive_file_name(short_uuid, client_type, extension)

        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime_type, resumable=False)

        try:
            if existing_file_id:
                logger.info(f"Updating Google Drive subscription file {existing_file_id}")
                service.files().update(
                    fileId=existing_file_id,
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
                file_id = existing_file_id
            else:
                metadata = {
                    "name": file_name,
                    "mimeType": mime_type,
                }

                if settings.GDRIVE_SUBSCRIPTIONS_FOLDER_ID:
                    metadata["parents"] = [settings.GDRIVE_SUBSCRIPTIONS_FOLDER_ID]

                logger.info(f"Creating new Google Drive subscription file '{file_name}'")
                created = service.files().create(
                    body=metadata,
                    media_body=media,
                    fields="id, webViewLink, webContentLink",
                    supportsAllDrives=True,
                ).execute()
                file_id = created.get("id")

                if not file_id:
                    raise RuntimeError("Google Drive did not return file ID for the created subscription")

                if settings.GDRIVE_MAKE_PUBLIC:
                    self._ensure_public_permission(file_id)

            file_metadata = service.files().get(
                fileId=file_id,
                fields="id, webViewLink, webContentLink",
                supportsAllDrives=True,
            ).execute()

            share_link = settings.format_gdrive_share_link(
                file_id,
                default=file_metadata.get("webContentLink") or file_metadata.get("webViewLink"),
            )

            if not share_link:
                share_link = file_metadata.get("webContentLink") or file_metadata.get("webViewLink")

            return file_id, share_link
        except HttpError as exc:
            logger.error(f"Google Drive API error: {exc}")
        except Exception as exc:
            logger.error(f"Failed to publish subscription to Google Drive: {exc}")

        return existing_file_id, None

    def _ensure_public_permission(self, file_id: str) -> None:
        service = self._get_service()

        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
            ).execute()
            logger.debug(f"Granted public read permission for Google Drive file {file_id}")
        except HttpError as exc:
            if exc.resp.status == 403:
                logger.warning(
                    "Insufficient permissions to make Google Drive file public. "
                    "Users might not have access to the subscription link."
                )
            else:
                logger.error(f"Failed to update Google Drive permissions: {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error while setting Google Drive permissions: {exc}")


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
