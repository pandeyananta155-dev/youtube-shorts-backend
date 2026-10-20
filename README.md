# YouTube Shorts Automation Backend

Phase 1 and 2 foundation for a locally runnable YouTube Shorts automation backend. It provides FastAPI, SQLite persistence, YouTube Data API v3 discovery, transparent trend scoring, and candidate shortlist endpoints. Discovery stores metadata only: it does not download or copy footage, generate videos, or upload content.

## Requirements

- Python 3.12 or newer
- FFmpeg is not required for Phases 1 or 2; it may be needed by a future video-processing phase.
- A YouTube Data API v3 key is optional for startup, health checks, browsing stored candidates, and tests. It is required for live discovery.
- No OAuth credentials, paid AI services, or external database are required.
- Runtime packages are listed in `requirements.txt`: FastAPI, Uvicorn, SQLAlchemy, Pydantic Settings, HTTPX, and pytest.

## Setup

From the repository root in Codespaces or a local terminal:

```bash
python --version
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp -n .env.example .env
```

On Windows PowerShell, use these activation/install commands instead:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env -ErrorAction SilentlyContinue
```

If PowerShell reports that script execution is disabled, activate the environment using your normal local Python policy or use Command Prompt with `.venv\Scripts\activate.bat`. `.env` is ignored by Git; never commit API keys or OAuth tokens. The checked-in `.env.example` is safe to copy and contains no secret.

### Configure YouTube access

1. In Google Cloud Console, create/select a project and enable **YouTube Data API v3**.
2. Create an API key under **APIs & Services → Credentials**. Restrict it to YouTube Data API v3 and follow Google's current key-restriction guidance.
3. Put the key in the local, gitignored `.env` as `YOUTUBE_API_KEY=...`, then restart the server.

The server-side provider never returns the key or includes it in application logs. Without a key, discovery returns HTTP 503; it does not fabricate live search results. Tests inject mock providers and make no Google requests.

## Initialize and run

Initialize the SQLite database explicitly:

```bash
python -m app.init_db
```

Start the development server on port 8000:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The application also initializes/migrates tables on startup. The additive migration extends the existing `source_videos` table without removing Phase 1 rows. In Codespaces, open forwarded port 8000. Visit `/health` for service/database status and `/docs` for interactive Swagger documentation. Phase 2 endpoints are under `/api/v1`.

## Tests

```bash
pytest
```

Tests use temporary SQLite databases and do not require network access or credentials.

## Configuration

Settings are loaded from environment variables and the optional root `.env` file. Environment variables take precedence. The default database is `sqlite:///./data/shorts.db`; its directory is created automatically. Copy `.env.example` to `.env` for local overrides, then restart Uvicorn after editing it.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `YouTube Shorts Automation Backend` | FastAPI title shown in `/docs`. |
| `ENVIRONMENT` | `development` | Deployment/environment label. |
| `LOG_LEVEL` | `INFO` | Structured log level: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `DATABASE_URL` | `sqlite:///./data/shorts.db` | SQLAlchemy database URL. SQLite is the default and only infrastructure required. |
| `YOUTUBE_API_KEY` | unset | Secret key for official YouTube Data API v3 calls. Required only for live discovery; never commit it. |
| `YOUTUBE_REQUEST_TIMEOUT_SECONDS` | `10` | Per-request HTTP timeout in seconds; valid range is greater than 0 and at most 120. |
| `YOUTUBE_MAX_QUERIES_PER_REQUEST` | `5` | Maximum expanded term/category searches in one API request; valid range is 1–100. |
| `SUPPORTED_CATEGORIES_CSV` | `celebrities,music,rap,documentaries,animation,gaming,education,comedy,sports,children` | Comma-separated allowed categories. |
| `RANKING_RECENCY_WEIGHT` | `0.20` | Relative weight for publication recency. |
| `RANKING_VIEWS_WEIGHT` | `0.45` | Relative weight for age-adjusted views per day. |
| `RANKING_ENGAGEMENT_WEIGHT` | `0.25` | Relative weight for available like/view ratio. |
| `RANKING_VELOCITY_WEIGHT` | `0.10` | Relative weight for observed view velocity. |
| `RANKING_RECENCY_HALF_LIFE_DAYS` | `7` | Publication age at which recency score halves. |
| `RANKING_VIEWS_PER_DAY_REFERENCE` | `100000` | Reference used to normalize views/day. |
| `RANKING_ENGAGEMENT_REFERENCE` | `0.05` | Like/view ratio normalized to an engagement score of 100. |
| `RANKING_MIN_VELOCITY_INTERVAL_HOURS` | `1` | Minimum interval between observed view samples before velocity is included. |

Ranking weights must be nonnegative and have a positive sum. The formula and missing-data behavior are described in [docs/api-testing.md](docs/api-testing.md).

## Phase 2 API and ranking

Try these routes in Swagger at `http://localhost:8000/docs`:

- `POST /api/v1/discovery/search` searches YouTube and inserts or refreshes candidates by video ID.
- `POST /api/v1/trends/evaluate` computes and stores ranking scores and factors.
- `GET /api/v1/videos/candidates` lists candidates with pagination and filters.
- `GET /api/v1/videos/candidates/{video_id}` returns candidate metadata and ranking factors.
- `GET /api/v1/trends/top` returns up to seven ranked candidates by default.
- `GET /api/v1/categories` returns configured categories: celebrities, music, rap, documentaries, animation, gaming, education, comedy, sports, and children's content by default.

See [docs/api-testing.md](docs/api-testing.md) for request/response examples and errors for each endpoint.

### Ranking model

Each available factor is normalized to 0–100; the final score is the weighted mean of available factors only:

- Recency: `100 × 2^(-age_days / half_life_days)`.
- Views/day: `min(100, 100 × ln(1 + views / max(age_days, 1/24)) / ln(1 + reference_views_per_day))`.
- Engagement: `min(100, 100 × (likes / views) / reference_engagement)` when likes and positive views are available.
- View velocity: uses the views/day normalization over at least two timestamped view samples when their interval meets `RANKING_MIN_VELOCITY_INTERVAL_HOURS` and counts did not decrease.

Default weights are 0.20 recency, 0.45 views, 0.25 engagement, and 0.10 velocity. Missing metrics are omitted rather than estimated, and weights are renormalized over observed factors. The score, factors, and evaluation time are stored for audit. Views/day adjusts lifetime views for publication age but assumes roughly even accumulation. This is a configurable heuristic, not a viral prediction or a replica of YouTube recommendations; it does not include impressions, watch time, channel size, audience fit, or all regional/contextual effects.

### Quota and troubleshooting

Quota is managed by Google Cloud and may change. The commonly documented default is 10,000 units/day; `search.list` is commonly 100 units and `videos.list` 1 unit. Each query can make one call to each endpoint. Check Google's current quota table and your project console before increasing `YOUTUBE_MAX_QUERIES_PER_REQUEST`. `max_results` limits results per search, not the number of searches. Quota exhaustion returns HTTP 429; invalid/restricted keys and rejected parameters return clear API errors. Requests are not automatically retried, avoiding hidden quota use.

## Project layout

```text
app/
	config.py          Environment-backed settings
	database.py        SQLAlchemy setup and additive migration
	discovery/         Provider, schemas, routes, and ranking service
	errors.py          Consistent API error responses
	init_db.py         Explicit database initialization command
	logging_config.py  Structured JSON logging
	main.py            FastAPI app factory and route registration
	models.py          Workflow and discovered-candidate models
docs/
	api-testing.md     Endpoint, ranking, quota, and test guide
tests/
	test_api.py        API and additive migration tests
	test_discovery.py  Mocked discovery and ranking tests
```

## Scope and next phases

The initial tables remain `SourceVideo`, `GeneratedShort`, `PublishingJob`, and `UploadHistory`. Phase 2 reuses `SourceVideo` for candidates, identified by the existing unique platform video ID. Future FFmpeg generation and authorized YouTube OAuth/upload workflows are intentionally not implemented here.