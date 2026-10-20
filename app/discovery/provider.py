import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass(frozen=True)
class SearchQuery:
    query: str
    category: str | None
    country_code: str | None
    language_code: str | None
    published_after: datetime | None
    published_before: datetime | None
    max_results: int


@dataclass(frozen=True)
class DiscoveredVideo:
    video_id: str
    title: str
    channel_name: str | None
    published_at: datetime | None
    duration_seconds: int | None
    view_count: int | None
    like_count: int | None


class DiscoveryProvider(Protocol):
    def search(self, query: SearchQuery) -> list[DiscoveredVideo]: ...


class YouTubeProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class MissingYouTubeApiKey(YouTubeProviderError):
    def __init__(self) -> None:
        super().__init__("YOUTUBE_API_KEY is not configured", status_code=503)


def parse_duration(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class YouTubeDataApiProvider:
    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def search(self, query: SearchQuery) -> list[DiscoveredVideo]:
        if not self._api_key:
            raise MissingYouTubeApiKey()

        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "q": query.query,
            "maxResults": query.max_results,
            "key": self._api_key,
        }
        if query.country_code:
            params["regionCode"] = query.country_code.upper()
        if query.language_code:
            params["relevanceLanguage"] = query.language_code
        if query.published_after:
            params["publishedAfter"] = query.published_after.isoformat()
        if query.published_before:
            params["publishedBefore"] = query.published_before.isoformat()

        try:
            with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
                search_response = client.get(f"{YOUTUBE_API_BASE}/search", params=params)
                self._raise_for_api_error(search_response)
                items = search_response.json().get("items", [])
                video_ids = [item.get("id", {}).get("videoId") for item in items]
                video_ids = [video_id for video_id in video_ids if video_id]
                if not video_ids:
                    return []

                videos_response = client.get(
                    f"{YOUTUBE_API_BASE}/videos",
                    params={
                        "part": "snippet,contentDetails,statistics",
                        "id": ",".join(video_ids),
                        "key": self._api_key,
                    },
                )
                self._raise_for_api_error(videos_response)
        except httpx.TimeoutException as exc:
            raise YouTubeProviderError("YouTube Data API request timed out", 504) from exc
        except httpx.RequestError as exc:
            raise YouTubeProviderError("Could not connect to the YouTube Data API", 502) from exc

        discovered: list[DiscoveredVideo] = []
        for item in videos_response.json().get("items", []):
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            details = item.get("contentDetails", {})
            video_id = item.get("id")
            title = snippet.get("title")
            if not video_id or not title:
                continue
            discovered.append(
                DiscoveredVideo(
                    video_id=video_id,
                    title=title,
                    channel_name=snippet.get("channelTitle"),
                    published_at=parse_timestamp(snippet.get("publishedAt")),
                    duration_seconds=parse_duration(details.get("duration")),
                    view_count=self._parse_count(statistics.get("viewCount")),
                    like_count=self._parse_count(statistics.get("likeCount")),
                )
            )
        return discovered

    @staticmethod
    def _parse_count(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _raise_for_api_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        status_code = response.status_code
        message = "YouTube Data API request failed"
        try:
            error = response.json().get("error", {})
            reasons = [item.get("reason", "") for item in error.get("errors", [])]
            if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
                status_code = 429
                message = "YouTube Data API quota exceeded"
            elif status_code == 403:
                message = "YouTube Data API rejected the key or request"
            elif status_code == 400:
                message = "YouTube Data API rejected the search parameters"
        except (ValueError, AttributeError):
            pass
        logger.warning("YouTube Data API returned HTTP %s", response.status_code)
        raise YouTubeProviderError(message, status_code=status_code if status_code >= 400 else 502)