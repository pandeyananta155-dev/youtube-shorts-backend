from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import create_database_engine
from app.discovery.provider import (
    DiscoveredVideo,
    SearchQuery,
    YouTubeDataApiProvider,
    YouTubeProviderError,
)
from app.discovery.ranking import calculate_score
from app.main import create_app
from app.models import SourceVideo


class FakeProvider:
    def __init__(self, by_category=None, error=None):
        self.by_category = by_category or {}
        self.error = error
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return self.by_category.get(query.category, self.by_category.get(None, []))


@pytest.fixture
def api_factory(tmp_path):
    created_engines = []

    def create(provider=None, **setting_overrides):
        database_url = f"sqlite:///{tmp_path / f'discovery-{len(created_engines)}.db'}"
        engine = create_database_engine(database_url)
        created_engines.append(engine)
        settings = Settings(database_url=database_url, **setting_overrides)
        app = create_app(settings=settings, engine=engine, discovery_provider=provider)
        return TestClient(app)

    yield create
    for engine in created_engines:
        engine.dispose()


def video(video_id, *, views=1000, likes=50, published_at=None):
    return DiscoveredVideo(
        video_id=video_id,
        title=f"Video {video_id}",
        channel_name="Channel",
        published_at=published_at or datetime.now(timezone.utc) - timedelta(days=1),
        duration_seconds=90,
        view_count=views,
        like_count=likes,
    )


def test_search_stores_video_metadata_and_deduplicates_refresh(api_factory):
    provider = FakeProvider({None: [video("youtube001")]})
    with api_factory(provider) as client:
        first = client.post("/api/v1/discovery/search", json={"search_terms": ["music"]})
        provider.by_category[None] = [video("youtube001", views=None, likes=None)]
        second = client.post("/api/v1/discovery/search", json={"search_terms": ["music"]})
        provider.by_category[None] = [video("youtube001", views=2500, likes=80)]
        third = client.post("/api/v1/discovery/search", json={"search_terms": ["music"]})

        detail = client.get("/api/v1/videos/candidates/youtube001")

    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert second.status_code == 200
    assert second.json()["created"] == 0
    assert second.json()["refreshed"] == 1
    assert third.json()["refreshed"] == 1
    assert detail.json()["view_count"] == 2500
    assert detail.json()["like_count"] == 80
    assert detail.json()["video_url"].endswith("youtube001")


def test_search_validates_configured_categories_and_query_budget(api_factory):
    provider = FakeProvider()
    with api_factory(provider, youtube_max_queries_per_request=1) as client:
        unknown = client.post("/api/v1/discovery/search", json={"categories": ["unknown"]})
        over_budget = client.post(
            "/api/v1/discovery/search",
            json={"search_terms": ["one", "two"]},
        )
        categories = client.get("/api/v1/categories")

    assert unknown.status_code == 422
    assert over_budget.status_code == 422
    assert categories.status_code == 200
    assert {"music", "rap", "children"}.issubset(categories.json()["categories"])
    assert provider.calls == []


def test_search_passes_location_language_dates_and_max_results(api_factory):
    provider = FakeProvider()
    after = "2026-10-01T00:00:00Z"
    before = "2026-10-20T00:00:00Z"
    with api_factory(provider) as client:
        response = client.post(
            "/api/v1/discovery/search",
            json={
                "search_terms": ["animation"],
                "country_code": "us",
                "language_code": "en",
                "published_after": after,
                "published_before": before,
                "max_results": 3,
            },
        )

    assert response.status_code == 200
    query = provider.calls[0]
    assert query.country_code == "US"
    assert query.language_code == "en"
    assert query.published_after == datetime.fromisoformat(after.replace("Z", "+00:00"))
    assert query.published_before == datetime.fromisoformat(before.replace("Z", "+00:00"))
    assert query.max_results == 3


def test_invalid_search_request_uses_validation_error_envelope(api_factory):
    with api_factory(FakeProvider()) as client:
        response = client.post(
            "/api/v1/discovery/search",
            json={"search_terms": [], "published_after": "2026-10-01T00:00:00"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"]


def test_missing_credentials_and_provider_errors_are_clear(api_factory):
    request_body = {"search_terms": ["education"]}
    with api_factory() as client:
        missing_key = client.post("/api/v1/discovery/search", json=request_body)
    provider = FakeProvider(error=YouTubeProviderError("YouTube Data API quota exceeded", 429))
    with api_factory(provider) as client:
        quota_error = client.post("/api/v1/discovery/search", json=request_body)

    assert missing_key.status_code == 503
    assert "YOUTUBE_API_KEY" in missing_key.json()["error"]["message"]
    assert quota_error.status_code == 429
    assert "quota exceeded" in quota_error.json()["error"]["message"]


def test_filters_pagination_detail_and_top_shortlist(api_factory):
    provider = FakeProvider(
        {
            "music": [video("musicvideo1", views=50000), video("musicvideo2", views=5000)],
            "gaming": [video("gamevideo01", views=20000)],
        }
    )
    with api_factory(provider) as client:
        client.post("/api/v1/discovery/search", json={"categories": ["music"]})
        client.post("/api/v1/discovery/search", json={"categories": ["gaming"]})
        page = client.get("/api/v1/videos/candidates?page=1&page_size=1&category=music")
        filtered = client.get("/api/v1/videos/candidates?category=gaming&status=discovered")
        detail = client.get("/api/v1/videos/candidates/gamevideo01")
        evaluated = client.post("/api/v1/trends/evaluate", json={"limit": 10})
        top = client.get("/api/v1/trends/top?limit=2")

    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert filtered.json()["total"] == 1
    assert detail.json()["category"] == "gaming"
    assert evaluated.json()["evaluated"] == 3
    assert top.json()["count"] == 2
    assert top.json()["items"][0]["ranking_score"] >= top.json()["items"][1]["ranking_score"]
    assert "factors" in top.json()["items"][0]["ranking_factors"]


def test_missing_metrics_are_not_scored_as_observed_metrics(api_factory):
    provider = FakeProvider({None: [video("nometrics01", views=None, likes=None)]})
    with api_factory(provider) as client:
        client.post("/api/v1/discovery/search", json={"search_terms": ["documentary"]})
        evaluated = client.post(
            "/api/v1/trends/evaluate",
            json={"video_ids": ["nometrics01"]},
        )

    factors = evaluated.json()["items"][0]["ranking_factors"]["factors"]
    assert "recency" in factors
    assert "views_per_day" not in factors
    assert "engagement" not in factors
    assert "view_velocity" not in factors


def test_velocity_requires_sufficient_timestamped_observations():
    now = datetime.now(timezone.utc)
    candidate = SourceVideo(
        platform_video_id="velocity001",
        title="Velocity",
        source_url="https://www.youtube.com/watch?v=velocity001",
        published_at=now - timedelta(days=2),
        view_count=2000,
        like_count=None,
        metric_history=[
            {"sampled_at": (now - timedelta(minutes=30)).isoformat(), "view_count": 1500},
            {"sampled_at": now.isoformat(), "view_count": 2000},
        ],
    )
    settings = Settings(ranking_min_velocity_interval_hours=1)

    _, factors = calculate_score(candidate, settings, now=now)

    assert "view_velocity" not in factors["factors"]


def test_velocity_is_scored_from_sufficient_timestamped_observations():
    now = datetime.now(timezone.utc)
    candidate = SourceVideo(
        platform_video_id="velocity002",
        title="Velocity",
        source_url="https://www.youtube.com/watch?v=velocity002",
        published_at=now - timedelta(days=2),
        view_count=3000,
        metric_history=[
            {"sampled_at": (now - timedelta(hours=2)).isoformat(), "view_count": 1000},
            {"sampled_at": now.isoformat(), "view_count": 3000},
        ],
    )

    _, factors = calculate_score(candidate, Settings(), now=now)

    assert factors["factors"]["view_velocity"]["views_per_day"] == 24000


def test_youtube_provider_parses_api_metadata_and_optional_metrics():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={"items": [{"id": {"videoId": "abcdefghijk"}}]},
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "abcdefghijk",
                        "snippet": {
                            "title": "Documentary",
                            "channelTitle": "Publisher",
                            "publishedAt": "2026-10-19T12:00:00Z",
                        },
                        "contentDetails": {"duration": "PT1M30S"},
                        "statistics": {"viewCount": "1234"},
                    }
                ]
            },
        )

    provider = YouTubeDataApiProvider(
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    query = SearchQuery("documentary", None, "US", "en", None, None, 10)

    result = provider.search(query)

    assert result[0].title == "Documentary"
    assert result[0].duration_seconds == 90
    assert result[0].view_count == 1234
    assert result[0].like_count is None


def test_youtube_provider_surfaces_quota_errors_without_exposing_key():
    def handler(request):
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}]}},
        )

    provider = YouTubeDataApiProvider(
        "never-log-this-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(YouTubeProviderError) as raised:
        provider.search(SearchQuery("music", None, None, None, None, None, 10))

    assert raised.value.status_code == 429
    assert "never-log-this-key" not in str(raised.value)