# API and Testing Guide

The API runs on port 8000. Use the Codespaces forwarded URL in the browser, then open `/docs` for interactive Swagger. Locally the base URL is `http://localhost:8000`; the OpenAPI document is `/openapi.json`.

## Setup and start

Prerequisites: Python 3.12 or newer and a terminal in the repository root. FFmpeg, OAuth, external services, and a YouTube key are not needed for tests or local startup. The key is only needed to execute live discovery. Dependencies are declared in `requirements.txt` (FastAPI/Uvicorn, SQLAlchemy, Pydantic Settings, HTTPX, and pytest).

Linux/macOS, including GitHub Codespaces:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
```

Add your YouTube API key to the local, gitignored `.env`, then initialize and start:

```bash
python -m app.init_db
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run all tests with `pytest`. Tests use fake providers and HTTPX mock transports; they do not need a key or make external requests. The server also applies the additive SQLite schema migration on startup.

Windows PowerShell local setup:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env -ErrorAction SilentlyContinue
python -m app.init_db
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In Codespaces, the devcontainer installs requirements and forwards port 8000. If the browser cannot reach `localhost`, use the forwarded address in the VS Code **Ports** panel. Local `.env` and SQLite files are ignored by Git.

## Get a YouTube API key

In Google Cloud Console, create/select a project, enable **YouTube Data API v3**, create an API key under **APIs & Services → Credentials**, and restrict the key to that API. Put it in `.env`:

```dotenv
YOUTUBE_API_KEY=your_key_here
```

Never commit `.env`, paste the key into requests, or share it in logs. `.env.example` contains no credential. If the key is absent, discovery returns HTTP 503; it does not return mock or fabricated search results. To use controlled test data, inject a fake provider in tests.

## Discovery search

### `POST /api/v1/discovery/search`

Each request can specify up to 20 terms, configured categories, country, language, RFC3339 publication bounds, and results per query. When terms and categories are both supplied, a search is generated for each term/category pair. The number of generated searches cannot exceed `YOUTUBE_MAX_QUERIES_PER_REQUEST` (default 5).

Request:

```json
{
  "search_terms": ["live performance"],
  "categories": ["music"],
  "country_code": "US",
  "language_code": "en",
  "published_after": "2026-10-01T00:00:00Z",
  "published_before": "2026-10-20T00:00:00Z",
  "max_results": 10
}
```

Example response (`200`):

```json
{
  "queries_run": 1,
  "results_received": 1,
  "created": 1,
  "refreshed": 0,
  "candidates": [
    {
      "video_id": "VIDEO_ID_01",
      "title": "Example performance",
      "channel_name": "Example channel",
      "published_at": "2026-10-18T12:00:00Z",
      "duration_seconds": 185,
      "view_count": 42000,
      "like_count": null,
      "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_01",
      "category": "music",
      "source_query": "live performance music",
      "country_code": "US",
      "language_code": "en",
      "discovered_at": "2026-10-20T12:00:00Z",
      "metrics_updated_at": "2026-10-20T12:00:00Z",
      "status": "discovered",
      "ranking_score": null,
      "ranking_factors": null,
      "evaluated_at": null
    }
  ]
}
```

Counts shown as `null` were not supplied by YouTube. Repeating a search refreshes metadata for the same YouTube ID rather than inserting a duplicate. A missing metric in a refresh does not overwrite a previously observed value. No media bytes are fetched or saved.

Example command:

```bash
curl -X POST http://localhost:8000/api/v1/discovery/search \
  -H 'Content-Type: application/json' \
  -d '{"search_terms":["live performance"],"categories":["music"],"country_code":"US","language_code":"en","max_results":10}'
```

## Evaluate trends

### `POST /api/v1/trends/evaluate`

Evaluates up to `limit` most recently discovered candidates, or only the supplied `video_ids`. Factors, weights used, score, and evaluation timestamp are persisted on each candidate.

Request:

```json
{
  "video_ids": ["VIDEO_ID_01"],
  "limit": 100
}
```

Example response (`200`):

```json
{
  "evaluated": 1,
  "items": [
    {
      "video_id": "VIDEO_ID_01",
      "title": "Example performance",
      "channel_name": "Example channel",
      "published_at": "2026-10-18T12:00:00Z",
      "duration_seconds": 185,
      "view_count": 5000,
      "like_count": null,
      "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_01",
      "category": "music",
      "source_query": "live performance music",
      "country_code": "US",
      "language_code": "en",
      "discovered_at": "2026-10-20T12:00:00Z",
      "metrics_updated_at": "2026-10-20T12:00:00Z",
      "status": "discovered",
      "ranking_score": 72.33,
      "ranking_factors": {
        "algorithm": "shortlist-v1",
        "factors": {
          "recency": {"score": 82.06, "age_days": 2.0, "half_life_days": 7.0},
          "views_per_day": {"score": 68.0, "views_per_day": 2500.0, "reference_views_per_day": 100000.0}
        },
        "weights_used": {"recency": 0.2, "views_per_day": 0.45}
      },
      "evaluated_at": "2026-10-20T12:01:00Z"
    }
  ]
}
```

## List candidates

### `GET /api/v1/videos/candidates`

Supports `page` (default 1), `page_size` (default 20, maximum 100), `category`, `status`, `country_code`, `language_code`, `search` (title/channel substring), and `min_score`.

Example:

```bash
curl 'http://localhost:8000/api/v1/videos/candidates?page=1&page_size=10&category=music&min_score=50'
```

Example response (`200`):

```json
{
  "total": 1,
  "page": 1,
  "page_size": 10,
  "items": [
    {
      "video_id": "VIDEO_ID_01",
      "title": "Example performance",
      "channel_name": "Example channel",
      "published_at": "2026-10-18T12:00:00Z",
      "duration_seconds": 185,
      "view_count": 5000,
      "like_count": null,
      "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_01",
      "category": "music",
      "source_query": "live performance music",
      "country_code": "US",
      "language_code": "en",
      "discovered_at": "2026-10-20T12:00:00Z",
      "metrics_updated_at": "2026-10-20T12:00:00Z",
      "status": "discovered",
      "ranking_score": 72.33,
      "ranking_factors": {"algorithm": "shortlist-v1", "factors": {}, "weights_used": {}},
      "evaluated_at": "2026-10-20T12:01:00Z"
    }
  ]
}
```

## Candidate details

### `GET /api/v1/videos/candidates/{video_id}`

Example:

```bash
curl http://localhost:8000/api/v1/videos/candidates/VIDEO_ID_01
```

Returns one candidate object with the same fields as the list's `items`; `ranking_factors` contains the per-factor normalized scores and effective weights after evaluation. Unknown IDs return the standard `404` error envelope.

Example response (`200`):

```json
{
  "video_id": "VIDEO_ID_01",
  "title": "Example performance",
  "channel_name": "Example channel",
  "published_at": "2026-10-18T12:00:00Z",
  "duration_seconds": 185,
  "view_count": 5000,
  "like_count": null,
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_01",
  "category": "music",
  "source_query": "live performance music",
  "country_code": "US",
  "language_code": "en",
  "discovered_at": "2026-10-20T12:00:00Z",
  "metrics_updated_at": "2026-10-20T12:00:00Z",
  "status": "discovered",
  "ranking_score": 72.33,
  "ranking_factors": {"algorithm": "shortlist-v1", "factors": {}, "weights_used": {}},
  "evaluated_at": "2026-10-20T12:01:00Z"
}
```

## Top shortlist

### `GET /api/v1/trends/top`

Returns only evaluated candidates, ordered by descending score. `limit` defaults to 7 and is capped at 50; optional `category` filters the list.

Example:

```bash
curl 'http://localhost:8000/api/v1/trends/top?limit=7&category=music'
```

Example response (`200`):

```json
{
  "count": 1,
  "items": [
    {
      "video_id": "VIDEO_ID_01",
      "title": "Example performance",
      "channel_name": "Example channel",
      "published_at": "2026-10-18T12:00:00Z",
      "duration_seconds": 185,
      "view_count": 5000,
      "like_count": null,
      "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_01",
      "category": "music",
      "source_query": "live performance music",
      "country_code": "US",
      "language_code": "en",
      "discovered_at": "2026-10-20T12:00:00Z",
      "metrics_updated_at": "2026-10-20T12:00:00Z",
      "status": "discovered",
      "ranking_score": 72.33,
      "ranking_factors": {"algorithm": "shortlist-v1", "factors": {}, "weights_used": {}},
      "evaluated_at": "2026-10-20T12:01:00Z"
    }
  ]
}
```

## Categories

### `GET /api/v1/categories`

The list is configured by the comma-separated `SUPPORTED_CATEGORIES_CSV` environment setting.

Example response (`200`):

```json
{
  "categories": ["celebrities", "music", "rap", "documentaries", "animation", "gaming", "education", "comedy", "sports", "children"]
}
```

## Ranking calculation and limitations

The default factor weights are recency 0.20, views/day 0.45, engagement 0.25, and view velocity 0.10. Each available factor is normalized to 0–100, then:

```text
score = sum(factor_score * factor_weight) / sum(weights for available factors)
```

- Recency: `100 * 2^(-age_days / RANKING_RECENCY_HALF_LIFE_DAYS)`.
- Views/day: `min(100, 100 * ln(1 + view_count / max(age_days, 1/24)) / ln(1 + RANKING_VIEWS_PER_DAY_REFERENCE))`.
- Engagement: `min(100, 100 * (like_count / view_count) / RANKING_ENGAGEMENT_REFERENCE)` when both counts exist and views are positive.
- Velocity: applies the views/day normalization to the increase between timestamped view samples, only with at least two samples separated by `RANKING_MIN_VELOCITY_INTERVAL_HOURS` or more and a nonnegative count change.

Unavailable metrics are omitted, never estimated as zero. Velocity is omitted until sufficient observations exist. Views/day adjusts cumulative views for age, but assumes roughly even accumulation; this is not an instantaneous velocity measurement. The score is an auditable heuristic, not a viral prediction or a reproduction of YouTube's recommendation system. It omits important signals such as impressions, watch time, audience fit, and channel size.

Weights and references are configurable with the `RANKING_*` settings in `.env.example`. Weights must be non-negative and have a positive sum.

## Quota, errors, and troubleshooting

YouTube Data API quota is assigned and enforced by Google Cloud; consult the project's current quota page because limits/costs can change. Commonly documented values are 10,000 units/day by default, 100 units per `search.list`, and 1 unit per `videos.list`. Each search term/category query makes one `search.list` request and, when IDs are found, one `videos.list` request. The default `YOUTUBE_MAX_QUERIES_PER_REQUEST=5` bounds this app-side usage; increasing it can consume quota quickly. `max_results` is results per query and is limited to 50. No automatic retries are made.

Errors use this JSON envelope:

```json
{
  "error": {
    "code": "http_error",
    "message": "YOUTUBE_API_KEY is not configured",
    "details": null
  }
}
```

- `422 validation_error`: malformed body, date range, or field value.
- `422 http_error`: unsupported category or search expansion above the configured query budget.
- `503 http_error`: missing `YOUTUBE_API_KEY` or database unavailable on `/health`.
- `429 http_error`: YouTube quota exhausted.
- `502`/`403`-class provider errors: rejected key/request or network failure; check API enablement and key restrictions.
- `504 http_error`: upstream request timed out; adjust `YOUTUBE_REQUEST_TIMEOUT_SECONDS` if appropriate.
- `500 internal_server_error`: unexpected issue; details are kept out of the response and recorded in structured logs.

If discovery reports a missing key, confirm `.env` is in the repository root and restart Uvicorn. If the key is rejected, verify the API is enabled for the same Google Cloud project and that restrictions permit this API. If quota is exhausted, wait for reset or manage quota through Google Cloud; reducing `max_results` alone does not reduce the `search.list` cost.

## Tests and database

Run the complete suite:

```bash
pytest
```

Tests cover Phase 1 health/errors and database migration plus mocked discovery, metadata refresh/deduplication, missing metrics/credentials, provider quota errors, filtering, pagination, ranking, and velocity observation requirements.

Phase 2 reuses `source_videos`, whose unique `platform_video_id` prevents duplicate candidates. Existing Phase 1 rows are preserved. `metric_history` stores a bounded set of timestamped observed view counts for velocity evaluation. No footage is downloaded or copied by any endpoint.