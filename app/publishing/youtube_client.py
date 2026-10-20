import json
import logging
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httplib2
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class UploadResult:
    video_id: str
    privacy_status: str
    upload_status: str
    published_at: datetime | None
    publish_at: datetime | None
    title: str | None


class PublishingProvider(Protocol):
    def upload(
        self,
        credentials: Credentials,
        source_path: Path,
        title: str,
        description: str,
        privacy_status: str,
        publish_at: datetime | None = None,
    ) -> UploadResult: ...

    def video_status(self, credentials: Credentials, video_id: str) -> UploadResult | None: ...

    def channel_info(self, credentials: Credentials) -> tuple[str, str | None]: ...

    def revoke(self, token: str) -> None: ...

    def cancel_scheduled(self, credentials: Credentials, video_id: str) -> bool: ...


class YouTubeApiError(RuntimeError):
    def __init__(self, message: str, code: str, retryable: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.quota_exhausted = quota_exhausted


class UploadOutcomeUnknown(RuntimeError):
    """The upload may have reached YouTube, so it must be reconciled before retry."""


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class YouTubeDataApiPublisher:
    def __init__(self, service_factory=build, media_upload_factory=MediaFileUpload):
        self.service_factory = service_factory
        self.media_upload_factory = media_upload_factory

    def upload(
        self,
        credentials: Credentials,
        source_path: Path,
        title: str,
        description: str,
        privacy_status: str,
        publish_at: datetime | None = None,
    ) -> UploadResult:
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise YouTubeApiError("Completed Short output is missing or empty", "invalid_media")
        if privacy_status not in {"private", "unlisted", "public"}:
            raise YouTubeApiError("Unsupported YouTube privacy status", "invalid_privacy_status")
        status: dict[str, Any] = {"privacyStatus": privacy_status}
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.isoformat().replace("+00:00", "Z")
        video_body = {
            "snippet": {"title": title[:100], "description": description[:5000]},
            "status": status,
        }
        service = self.service_factory(
            "youtube", "v3", credentials=credentials, cache_discovery=False
        )
        media = self.media_upload_factory(
            str(source_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )
        try:
            request = service.videos().insert(part="snippet,status", body=video_body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            video_id = response.get("id")
            if not video_id:
                raise UploadOutcomeUnknown("YouTube upload completed without a returned video ID")
            returned_status = response.get("status", {})
            returned_snippet = response.get("snippet", {})
            return UploadResult(
                video_id=video_id,
                privacy_status=returned_status.get("privacyStatus", status["privacyStatus"]),
                upload_status=returned_status.get("uploadStatus", "uploaded"),
                published_at=_parse_datetime(returned_snippet.get("publishedAt")),
                publish_at=_parse_datetime(returned_status.get("publishAt")),
                title=returned_snippet.get("title", title),
            )
        except HttpError as exc:
            if 500 <= getattr(exc, "status_code", 0) < 600:
                raise UploadOutcomeUnknown(
                    "YouTube returned a server error during upload; reconcile the channel before retrying."
                ) from exc
            raise self._api_error(exc) from exc
        except (UploadOutcomeUnknown, YouTubeApiError):
            raise
        except (OSError, socket.timeout, TimeoutError, httplib2.HttpLib2Error) as exc:
            raise UploadOutcomeUnknown(
                "The network was interrupted during upload; reconcile the channel before retrying."
            ) from exc

    def video_status(self, credentials: Credentials, video_id: str) -> UploadResult | None:
        service = self.service_factory("youtube", "v3", credentials=credentials, cache_discovery=False)
        try:
            response = service.videos().list(part="snippet,status", id=video_id).execute()
        except HttpError as exc:
            raise self._api_error(exc) from exc
        items = response.get("items", [])
        if not items:
            return None
        item = items[0]
        status = item.get("status", {})
        snippet = item.get("snippet", {})
        return UploadResult(
            video_id=item["id"],
            privacy_status=status.get("privacyStatus", "private"),
            upload_status=status.get("uploadStatus", "uploaded"),
            published_at=_parse_datetime(snippet.get("publishedAt")),
            publish_at=_parse_datetime(status.get("publishAt")),
            title=snippet.get("title"),
        )

    def channel_info(self, credentials: Credentials) -> tuple[str, str | None]:
        service = self.service_factory("youtube", "v3", credentials=credentials, cache_discovery=False)
        try:
            response = service.channels().list(part="id,snippet", mine=True).execute()
        except HttpError as exc:
            raise self._api_error(exc) from exc
        items = response.get("items", [])
        if not items:
            raise YouTubeApiError("No YouTube channel is available for the authorized account", "channel_missing")
        channel = items[0]
        return channel["id"], channel.get("snippet", {}).get("title")

    def cancel_scheduled(self, credentials: Credentials, video_id: str) -> bool:
        service = self.service_factory("youtube", "v3", credentials=credentials, cache_discovery=False)
        try:
            response = service.videos().update(
                part="status",
                body={"id": video_id, "status": {"privacyStatus": "private"}},
            ).execute()
        except HttpError as exc:
            raise self._api_error(exc) from exc
        return response.get("status", {}).get("privacyStatus") == "private"

    @staticmethod
    def revoke(token: str) -> None:
        import requests

        try:
            response = requests.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": token},
                timeout=10,
            )
            if response.status_code not in {200, 400}:
                logger.warning("Google token revocation returned HTTP %s", response.status_code)
        except requests.RequestException:
            logger.warning("Google token revocation request failed")

    @staticmethod
    def refresh_credentials(credentials: Credentials) -> Credentials:
        try:
            credentials.refresh(GoogleRequest())
            return credentials
        except GoogleAuthError as exc:
            raise YouTubeApiError("Google authorization expired or was revoked; reconnect the channel", "reauth_required") from exc

    @staticmethod
    def _api_error(exc: HttpError) -> YouTubeApiError:
        status = getattr(exc, "status_code", 0)
        reason = "youtube_api_error"
        try:
            payload = json.loads(exc.content.decode("utf-8"))
            error = payload.get("error", {})
            errors = error.get("errors", [])
            if errors:
                reason = str(errors[0].get("reason") or reason)
        except (ValueError, AttributeError, UnicodeDecodeError):
            pass
        if reason in {"quotaExceeded", "dailyLimitExceeded"}:
            return YouTubeApiError("YouTube API quota exhausted", reason, quota_exhausted=True)
        if status == 401 or reason in {"authError", "invalidCredentials"}:
            return YouTubeApiError("Google authorization expired or was revoked; reconnect the channel", "reauth_required")
        retryable = status in {408, 429, 500, 502, 503, 504} or reason in {"backendError", "rateLimitExceeded"}
        return YouTubeApiError("YouTube API rejected the publishing request", reason, retryable=retryable)