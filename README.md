# YouTube Shorts Automation Backend

Phase 1–4 foundation for a locally runnable YouTube Shorts automation backend. It provides FastAPI, SQLite persistence, YouTube Data API v3 discovery/ranking, authorized local video rendering, owner-authorized OAuth publishing, resumable uploads, and a configurable scheduler. Real uploads are disabled by default and require explicit owner controls.

## Requirements

- Python 3.12 or newer
- FFmpeg and ffprobe are required to render/validate videos. Phase 1/2 APIs and tests can run without them. eSpeak NG is optional and only needed for local narration.
- A YouTube Data API v3 key is optional for startup, health checks, browsing stored candidates, and tests. It is required for live discovery.
- No OAuth credentials are needed for tests or local health/discovery/generation work. Google OAuth setup is required for real account publishing.
- Runtime packages are listed in `requirements.txt`: FastAPI, Uvicorn, SQLAlchemy, Pydantic Settings, HTTPX, python-multipart, Google OAuth/API clients, cryptography, and pytest.

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

Install FFmpeg locally if it is missing (Ubuntu/Debian/Codespaces):

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
ffprobe -version
```

The Codespaces devcontainer installs FFmpeg and eSpeak NG during creation. eSpeak NG is optional; install it locally only if you want narration:

```bash
sudo apt-get install -y espeak-ng
espeak-ng --version
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

The application initializes/migrates tables on startup. Additive migrations preserve Phase 1–3 rows. In Codespaces, open forwarded port 8000. Visit `/health` for service/database status and `/docs` for interactive Swagger documentation. APIs are under `/api/v1`.

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
| `SOURCE_MEDIA_DIRECTORY` | `./data/media` | Local source-media directory. |
| `GENERATION_OUTPUT_DIRECTORY` | `./data/outputs` | Local rendered MP4 and caption directory. |
| `MAX_SOURCE_FILE_MB` | `500` | Maximum uploaded/downloaded source size. |
| `MAX_OUTPUT_FILE_MB` | `1000` | Maximum rendered output size. |
| `MEDIA_DOWNLOAD_TIMEOUT_SECONDS` | `20` | Timeout for authorized direct-media URL downloads. |
| `DEFAULT_SHORT_DURATION_SECONDS` | `45` | Default target duration. |
| `MAX_SHORT_DURATION_SECONDS` | `180` | Maximum allowed requested/rendered duration. |
| `VIDEO_ASPECT_RATIO` | `9:16` | Target video aspect ratio. Dimensions must match. |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `1080` / `1920` | Output frame dimensions. |
| `VIDEO_FPS` | `30` | Output frame rate. |
| `VIDEO_CRF` / `VIDEO_PRESET` | `23` / `medium` | H.264 quality and encoder speed/size tradeoff. |
| `AUDIO_BITRATE` | `192k` | AAC audio bitrate. |
| `CAPTION_FONT_NAME` / `CAPTION_FONT_SIZE` | `DejaVu Sans` / `48` | Burned-in subtitle style. |
| `CAPTION_MARGIN_VERTICAL` / `CAPTION_OUTLINE_SIZE` | `120` / `3` | Subtitle safe margin and outline. |
| `CAPTION_PRIMARY_COLOR` / `CAPTION_OUTLINE_COLOR` | `&H00FFFFFF` / `&H00000000` | ASS subtitle colors. |
| `NARRATION_PROVIDER` | `none` | `none` or `espeak`; narration is opt-in per job. |
| `NARRATION_VOICE` / `NARRATION_SPEED` | `en` / `165` | eSpeak NG voice and words-per-minute setting. This is a generic synthetic voice, not voice cloning. |
| `ESPEAK_BINARY` / `FFMPEG_BINARY` / `FFPROBE_BINARY` | `espeak-ng` / `ffmpeg` / `ffprobe` | Executable names/paths for local media tools. |
| `MAX_GENERATION_ATTEMPTS` | `3` | Maximum attempts per job, including the initial render. |
| `CATEGORY_TEMPLATES` | `{}` | Optional JSON object mapping category names to templates using `{title}` and `{commentary}`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | unset | Google OAuth web client. Needed for channel authorization; keep the secret local. |
| `GOOGLE_OAUTH_REDIRECT_URI` | localhost callback | Must exactly match a Google Cloud authorized redirect URI. |
| `OAUTH_TOKEN_ENCRYPTION_KEY` | unset | Fernet key encrypting refresh tokens in SQLite. Back it up securely; losing it requires reconnecting. |
| `PUBLISHING_ADMIN_TOKEN` | unset | Owner bearer token protecting publishing mutations/settings; minimum 32 characters. |
| `PUBLISHING_DRY_RUN` | `true` | Default mode records jobs without uploading. |
| `ENABLE_PUBLIC_PUBLISHING` | `false` | Initial value for the owner-controlled public publishing gate. |
| `ALLOW_PRIVATE_TEST_UPLOADS` | `false` | Initial value for the explicit private/unlisted test upload gate. |
| `PUBLISHING_DAILY_TARGET` | `5` | Daily target, restricted to 5–7 Shorts. |
| `PUBLISHING_TIMEZONE` | `UTC` | IANA timezone for daily limits and windows. |
| `PUBLISHING_WINDOWS` | `[{"start":"10:00","end":"18:00"}]` | JSON array of local publishing windows. |
| `PUBLISHING_ALLOWED_DAYS` | `[0,1,2,3,4,5,6]` | JSON weekdays, Monday `0` through Sunday `6`. |
| `PUBLISHING_SCHEDULER_ENABLED` | `false` | Scheduler starts disabled. |
| `PUBLISHING_PAUSED_BY_DEFAULT` | `true` | Global publishing starts paused. |
| `PUBLISHING_REQUIRE_REVIEW` | `true` | Require owner approval before publishing. |
| `PUBLISHING_SCHEDULER_INTERVAL_SECONDS` | `60` | In-process due-job and status polling interval. |
| `PUBLISHING_MAX_RETRIES` | `3` | Bounded retries for known transient errors. |
| `PUBLISHING_UPLOAD_LEAD_MINUTES` | `30` | Start upload this long before YouTube's scheduled `publishAt` time. |

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

## Phase 3 video generation

### Rights and source handling

Only upload media you own or have explicit permission/license to reuse. Upload and direct-URL registration require a rights-verification attestation and license/permission details. This is a user attestation, not legal verification. Discovered YouTube candidates default to `REQUIRES_PERMISSION`; submitting a generation job for one will not invoke FFmpeg. Attach authorized media to that candidate first. Direct YouTube/watch/shorts/googlevideo URLs are blocked. Direct HTTPS media URLs must be public, end with a supported video extension, and return video bytes. URL fetching does not follow redirects and rejects private/local IPs.

Captions, cropping, narration, or commentary do not make someone else's footage copyright-safe. No DRM or access restrictions are bypassed. No background music is added. Phase 3 does not upload videos or use OAuth.

### Generate a Short

Start Uvicorn, open the Codespaces forwarded port 8000, and visit `/docs`. Upload a user-owned/licensed file using `POST /api/v1/shorts/sources/upload`, or register a directly downloadable authorized HTTPS asset using `POST /api/v1/shorts/sources/url`. Then submit its `source_id` to `POST /api/v1/shorts/generate`. Generation currently runs synchronously in the API request and returns the persisted job state/result. Use `GET /api/v1/shorts/jobs/{job_id}` to inspect it and `/preview` to download a completed MP4.

The generated MP4 and SRT captions are stored under `data/outputs/` by default; source files are under `data/media/`. Both directories are ignored by Git. Generated titles, descriptions, scripts, captions, rights details, settings, output metadata, and errors are stored in SQLite for review. Templates are deterministic and local. Optional eSpeak NG narration is generic synthesized speech and does not clone real people.

Codespace workspace storage is local to that Codespace. Files generally remain while it is stopped/restarted, but deleting the Codespace can delete its uncommitted media. Download finished videos via the preview endpoint and save them outside the Codespace for durable retention. Do not commit generated videos, source media, or database files.

## Phase 4 OAuth and publishing

See [docs/api-testing.md](docs/api-testing.md) for Google Cloud setup and the full Swagger workflow. Enable YouTube Data API v3, configure an OAuth consent screen and owner test user, create a **Web application** OAuth client, and register the exact local or Codespaces callback URI. Set the client ID/secret in `.env`. Generate `OAUTH_TOKEN_ENCRYPTION_KEY` and `PUBLISHING_ADMIN_TOKEN` locally; never share or commit them. Refresh tokens are Fernet-encrypted in SQLite. Back up the encryption key securely or reconnect the channel after losing it.

OAuth requests only `youtube.upload` and `youtube.readonly`. Publishing starts dry-run, globally paused, with review required and both public and private test uploads disabled. A private test upload requires explicitly enabling private test uploads and reviewing the Short; public publishing additionally requires `dry_run=false` and `public_publishing_enabled=true`. Google may restrict projects that have not completed its required API audit to private uploads; account eligibility and quota remain controlled by Google.

The scheduler assigns 5–7 eligible Shorts per local calendar day within the owner's IANA timezone, weekdays, and windows. It uploads scheduled videos privately with YouTube's `publishAt` field shortly before the configured time; `UPLOADED` is not considered `PUBLISHED` until YouTube confirms public availability. The in-process scheduler runs only while Uvicorn is alive; use one app worker with SQLite. A stopped/suspended Codespace cannot process jobs. For dependable schedules, use an always-on owner-controlled host. Global pause stops new local publishing work; videos already uploaded to YouTube with a future `publishAt` remain scheduled there until cancelled.

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
	generation/        Rights validation, providers, FFmpeg, job service, and routes
	publishing/        OAuth, encrypted credentials, upload service, scheduling, and routes
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
	test_generation.py Mocked media generation, permissions, and job lifecycle tests
	test_publishing.py Mocked OAuth, resumable upload, retries, scheduling, and safety tests
```

## Scope

Workflow tables remain `SourceVideo`, `GeneratedShort`, `PublishingJob`, and `UploadHistory`; Phase 4 adds OAuth connection/state and owner publishing settings. No frontend, paid AI/database service, or monetization/reach guarantee is included.