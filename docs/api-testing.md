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

## Phase 3: Authorized video generation

FFmpeg and ffprobe must be on `PATH` to render. The Codespaces devcontainer installs them automatically when the Codespace is created. If missing on Ubuntu/Debian or in an existing Codespace, run:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
ffprobe -version
```

eSpeak NG is optional. Install it only if local narration is required:

```bash
sudo apt-get install -y espeak-ng
espeak-ng --version
```

Generation uses only media you own or are authorized to reuse. The rights checkbox/form field is your attestation; the application cannot determine whether a license is legally valid. Discovered YouTube candidates start as `REQUIRES_PERMISSION`. Their metadata is never treated as permission to use the footage. Attach an authorized file or direct media URL to the candidate before retrying generation. YouTube watch/shorts, Googlevideo, private-network URLs, URL redirects, DRM bypasses, and access-control bypasses are not supported.

Captions, cropping, commentary, or narration do not make unlicensed footage copyright-safe. No music is added by this phase. Generated scripts are local deterministic drafts or user-supplied text; they must be reviewed. The only built-in narration is generic local eSpeak NG speech, never a cloned or impersonated real person's voice. Children's-category scripts/captions are rejected if they contain basic email/phone patterns; this is a safeguard, not a full content moderation system.

### Check tools and current settings

`GET /api/v1/shorts/settings` displays safe generation configuration and whether tools are available. It does not reveal absolute media paths or credentials.

```bash
curl http://localhost:8000/api/v1/shorts/settings
```

Example response:

```json
{
  "default_duration_seconds": 45,
  "maximum_duration_seconds": 180,
  "aspect_ratio": "9:16",
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "video_crf": 23,
  "video_preset": "medium",
  "audio_bitrate": "192k",
  "caption_font_name": "DejaVu Sans",
  "caption_font_size": 48,
  "caption_margin_vertical": 120,
  "caption_outline_size": 3,
  "caption_primary_color": "&H00FFFFFF",
  "caption_outline_color": "&H00000000",
  "narration_provider": "none",
  "narration_available": false,
  "ffmpeg_available": true,
  "ffprobe_available": true,
  "max_source_file_mb": 500,
  "max_output_file_mb": 1000,
  "output_directory": "outputs",
  "category_templates": {}
}
```

### Register a user-provided authorized file

`POST /api/v1/shorts/sources/upload` accepts multipart fields `file`, `creator`, `license_or_permission`, and `rights_verified`; `candidate_video_id` and `category` are optional. Supported suffixes: MP4, MOV, M4V, MKV, and WebM. The upload size is bounded by `MAX_SOURCE_FILE_MB` and probed by ffprobe before it is stored.

In Swagger, choose **Try it out**, select a small authorized MP4 file, enter creator and permission details, and set `rights_verified` to `true` only after verifying the rights yourself.

Example response (`201`):

```json
{
  "source_id": 17,
  "candidate_video_id": "local_27f4ab322b834b0abf4c110f784a210e",
  "title": "owned",
  "source_type": "user_file",
  "rights_verification_status": "VERIFIED",
  "rights_verified": true,
  "creator": "My production team",
  "media_attached": true
}
```

Do not supply a path or filename as a server path. The server generates a random storage name.

### Register an authorized direct media URL

`POST /api/v1/shorts/sources/url` downloads one directly accessible, public HTTPS video file only after the same permission attestation. YouTube URLs and private/local addresses are rejected; redirects are not followed. URLs need a supported media extension and `video/*` or `application/octet-stream` response.

Request:

```json
{
  "source_url": "https://media.example.org/owned-clip.mp4",
  "creator": "My production team",
  "license_or_permission": "Owned by our production team",
  "rights_verified": true,
  "category": "education"
}
```

The response has the same shape as file registration with `source_type: "authorized_url"`. Source URL contents are stored as attribution metadata; full URL credentials are rejected and URLs are not returned by source APIs.

### Submit generation

`POST /api/v1/shorts/generate` accepts exactly one of `source_id` (registered media) or `candidate_video_id` (Phase 2 candidate). Optional fields include a title, category, original commentary or script, captions, duration, and `include_narration`. An identical source/configuration returns the same job rather than creating a duplicate. Generation runs synchronously in the request; this phase does not add a background queue.

Request after source registration:

```json
{
  "source_id": 17,
  "title": "How this optical illusion works",
  "category": "education",
  "commentary": "Here is an original explanation of the visual effect.",
  "duration_seconds": 30,
  "include_narration": false
}
```

The response includes `job_id`, status, reviewable title/description/script/captions, render dimensions, output filename/preview URL on success, attempt count, and useful errors on failure. Typical terminal statuses are `COMPLETED`, `FAILED`, or `REQUIRES_PERMISSION`.

Example completed response (`201`):

```json
{
  "job_id": 42,
  "source_id": 17,
  "candidate_video_id": "local_27f4ab322b834b0abf4c110f784a210e",
  "status": "COMPLETED",
  "attempt_count": 1,
  "error": null,
  "draft_title": "How this optical illusion works",
  "draft_description": "Original commentary: Here is an original explanation of the visual effect.\n\n#Shorts #education",
  "script": "Original commentary: Here is an original explanation of the visual effect.",
  "captions": "1\n00:00:00,000 --> 00:00:30,000\nOriginal commentary: Here is an original explanation of the visual effect.\n",
  "narration": null,
  "duration_seconds": 30,
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "file_size_bytes": 18324567,
  "output_filename": "short_42.mp4",
  "preview_url": "/api/v1/shorts/jobs/42/preview",
  "created_at": "2026-10-20T12:00:00Z",
  "started_at": "2026-10-20T12:00:01Z",
  "completed_at": "2026-10-20T12:00:22Z"
}
```

For an unauthorized discovered candidate, the job is persisted with `REQUIRES_PERMISSION`; FFmpeg is not called. Register/attach an authorized file with `candidate_video_id`, then retry that job.

### Get job status and errors

`GET /api/v1/shorts/jobs/{job_id}` returns the job response above, including draft text and the latest error.

```bash
curl http://localhost:8000/api/v1/shorts/jobs/42
```

Job states are `PENDING`, `VALIDATING_SOURCE`, `GENERATING_SCRIPT`, `RENDERING`, `COMPLETED`, `REQUIRES_PERMISSION`, and `FAILED`. Intermediate stages are persisted before processing; failures include a sanitized message and incremented attempt count.

### List jobs

`GET /api/v1/shorts/jobs` supports `page`, `page_size` (maximum 100), `status`, and `source_id`.

```bash
curl 'http://localhost:8000/api/v1/shorts/jobs?page=1&page_size=20&status=COMPLETED'
```

Response: `{ "total": 1, "page": 1, "page_size": 20, "items": [ ...job response... ] }`.

### Retry a failed or newly authorized job

`POST /api/v1/shorts/jobs/{job_id}/retry` retries a failed job, or one blocked by missing permission after its source has been authorized and media attached. Retries are capped by `MAX_GENERATION_ATTEMPTS` (default 3); completed jobs and other active states cannot be retried.

```bash
curl -X POST http://localhost:8000/api/v1/shorts/jobs/42/retry
```

The endpoint returns the updated job response. A failed render stays recorded as `FAILED` with its error rather than disappearing.

### Preview or download output

`GET /api/v1/shorts/jobs/{job_id}/preview` streams the completed MP4 as `video/mp4` with a server-generated filename. It returns `409` unless the job completed and `404` if its output is missing/outside the configured directory. The endpoint never accepts a filesystem path.

```bash
curl -L http://localhost:8000/api/v1/shorts/jobs/42/preview -o short_42.mp4
```

Default storage locations are `data/media/` (sources), `data/outputs/` (MP4 and SRT), and `data/shorts.db` (SQLite). These are ignored by Git. Codespace storage persists while the Codespace is stopped but is not a backup and may be deleted with the Codespace. Download finished work through the preview endpoint and save it outside the Codespace for durable retention; do not commit media files.

### Local narration

Narration is disabled by default. Install eSpeak NG and configure `NARRATION_PROVIDER=espeak` to enable the optional local synthetic voice; set `include_narration: true` per request. Ubuntu/Codespaces:

```bash
sudo apt-get update
sudo apt-get install -y espeak-ng
espeak-ng --version
```

If narration is requested while the provider is `none` or unavailable, the job fails clearly and preserves its script/captions for review. No paid AI provider or voice cloning is included.

## Phase 4: OAuth and publishing

All publishing mutations, settings, job lists/details, schedules, and history require the owner admin bearer token. OAuth callback is protected by a one-time state value stored as a hash with a 10-minute expiry. `GET /api/v1/youtube/auth/status` is read-only and does not return credentials. OAuth uses only `youtube.upload` and `youtube.readonly`. Refresh tokens are Fernet-encrypted in SQLite; access tokens, refresh tokens, client secrets, and authorization codes are never included in response payloads or application logs.

### Google Cloud and OAuth setup

1. In Google Cloud Console, create/select the project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen. For an External app in Testing, add the account owner as a test user. Google may expire testing refresh tokens after a limited period; follow the current Google consent-screen requirements.
3. Create an OAuth client ID of type **Web application**. Add the exact callback URI as an Authorized redirect URI:
   - Local: `http://localhost:8000/api/v1/youtube/auth/callback`
   - Codespaces: `https://<CODESPACE_NAME>-8000.app.github.dev/api/v1/youtube/auth/callback`
4. Put the Google client ID, secret, and matching redirect URI in the local `.env`. Do not commit `.env`.
5. Generate two independent secrets locally and store them only in `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use the first value for `OAUTH_TOKEN_ENCRYPTION_KEY` and the second for `PUBLISHING_ADMIN_TOKEN`. The bearer token must be at least 32 characters. Back up the encryption key securely; without it, stored refresh tokens cannot be decrypted and the account must reconnect. Empty entries in `.env.example` are placeholders and are ignored.

Google may require an OAuth verification/API compliance audit before videos can be uploaded publicly. Some unverified API projects are restricted to private uploads. Channel/account restrictions, quota, consent-screen review status, and current Google policy cannot be overridden by this application.

### Authorize or disconnect the channel

In Swagger, authorize admin operations by sending `Authorization: Bearer <PUBLISHING_ADMIN_TOKEN>` on each protected operation. The token is an owner-generated local secret, not a Google token.

`GET /api/v1/youtube/auth/status` returns the safe connection state:

```json
{"connected": false, "status": "DISCONNECTED", "channel_title": null, "connected_at": null, "last_verified_at": null}
```

1. Call `GET /api/v1/youtube/auth/start` with the admin bearer token. It returns an `authorization_url`; open it in a browser and approve the YouTube channel access.
2. Google redirects to the registered callback. On success, the callback returns channel status/title only, never OAuth code or tokens.
3. Call `GET /api/v1/youtube/auth/status` to verify the connected channel.
4. Call `POST /api/v1/youtube/auth/disconnect` with the admin bearer token to revoke with Google where possible and always clear the local encrypted token. If a token is expired/revoked, the connection becomes `REAUTH_REQUIRED`; authorize again.

OAuth setup/configuration problems return HTTP 503. Denied consent or invalid/expired/reused state returns HTTP 400. If callback URI mismatch occurs, make the `.env` URI exactly match the Google Cloud URI and restart Uvicorn.

### Publishing settings

`GET /api/v1/publishing/settings` requires admin bearer auth and returns owner controls without secrets:

```json
{
  "daily_target": 5,
  "timezone": "UTC",
  "publishing_windows": [{"start": "10:00", "end": "18:00"}],
  "allowed_days": [0, 1, 2, 3, 4, 5, 6],
  "scheduler_enabled": false,
  "globally_paused": true,
  "require_review": true,
  "dry_run": true,
  "public_publishing_enabled": false,
  "allow_private_test_uploads": false,
  "max_retries": 3,
  "upload_lead_minutes": 30
}
```

Example read:

```bash
curl http://localhost:8000/api/v1/publishing/settings \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
```

`PUT /api/v1/publishing/settings` replaces these settings. `daily_target` must be 5–7; timezone must be an IANA name; windows use `HH:MM` 24-hour local time and cannot overlap; days are Monday `0` through Sunday `6`. Public publishing cannot be enabled while dry-run remains true.

Example settings update for safe dry-run testing:

```json
{
  "daily_target": 5,
  "timezone": "America/Los_Angeles",
  "publishing_windows": [{"start": "10:00", "end": "18:00"}],
  "allowed_days": [0, 1, 2, 3, 4, 5, 6],
  "scheduler_enabled": false,
  "globally_paused": false,
  "require_review": true,
  "dry_run": true,
  "public_publishing_enabled": false,
  "allow_private_test_uploads": false,
  "max_retries": 3,
  "upload_lead_minutes": 30
}
```

### Dry-run and private test upload

Start with dry-run. It creates a job and records reviewable data but makes no YouTube upload request. The generated Short must be `COMPLETED`, source rights must be `VERIFIED`, and the owner must set `review_confirmed: true`.

`POST /api/v1/publishing/jobs` request:

```json
{
  "generated_short_id": 42,
  "privacy_status": "private",
  "review_confirmed": true,
  "idempotency_key": "owner-short-42-review-1",
  "title": "Owner-reviewed title",
  "description": "Owner-reviewed description #Shorts"
}
```

Same source/Short returns the existing job. The `idempotency_key` is additionally checked to prevent accidental reuse for a different Short. Job response includes status, edited title/description, dry-run flag, attempts, YouTube ID/URL if returned, errors, and timestamps.

Example dry-run response (`201`):

```json
{
  "job_id": 13,
  "generated_short_id": 42,
  "title": "Owner-reviewed title",
  "description": "Owner-reviewed description #Shorts",
  "status": "PENDING",
  "privacy_status": "private",
  "dry_run": true,
  "review_confirmed": true,
  "scheduled_at": null,
  "video_id": null,
  "video_url": null,
  "attempts": 0,
  "max_attempts": 3,
  "last_error": "Dry run: no YouTube upload was attempted",
  "api_error_code": null,
  "next_attempt_at": null,
  "outcome_unknown": false,
  "created_at": "2026-10-20T12:00:00Z",
  "updated_at": "2026-10-20T12:00:00Z"
}
```

After reviewing a dry-run job, enable a private test upload in settings: set `dry_run=false`, `globally_paused=false`, `allow_private_test_uploads=true`, keep `public_publishing_enabled=false`, and keep `require_review=true`. Then call `POST /api/v1/publishing/jobs/{job_id}/retry` to explicitly promote that pending dry-run job. The endpoint validates the output again with ffprobe, checks rights/review/OAuth, and sends a resumable upload using the private privacy setting. Do not use a public privacy setting until the owner has explicitly enabled public publishing and the Google project/channel are eligible.

### Enable public publishing

Only after the private test succeeds and is reviewed, use `PUT /api/v1/publishing/settings` with `dry_run=false`, `public_publishing_enabled=true`, desired review settings, and the chosen 5–7/day target, timezone, windows, and allowed days. A public job is rejected/held as `NEEDS_REVIEW` if this gate is off. The API does not promise reach or monetization.

### Jobs, retries, history, and errors

- `POST /api/v1/publishing/jobs`: create a job for a completed Short, with owner-edited title/description, privacy, review confirmation, and optional idempotency key.
- `GET /api/v1/publishing/jobs`: paginated list; filter by `status` and `privacy_status`.
- `GET /api/v1/publishing/jobs/{job_id}`: retrieve state and last error.
- `POST /api/v1/publishing/jobs/{job_id}/retry`: retry known eligible failures after backoff; also explicitly promotes a dry-run pending job after dry-run has been disabled. Unknown upload outcomes are reconciled first and never blindly retried.
- `GET /api/v1/publishing/history`: paginated upload-attempt history with YouTube ID/URL, visibility, error code, and confirmation timestamp.

Use the owner bearer token for each:

```bash
curl 'http://localhost:8000/api/v1/publishing/jobs?page=1&page_size=20&status=UPLOADED' \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
curl http://localhost:8000/api/v1/publishing/jobs/13 \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
curl -X POST http://localhost:8000/api/v1/publishing/jobs/13/retry \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
curl 'http://localhost:8000/api/v1/publishing/history?page=1&page_size=20' \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
```

The paginated job response is `{ "total": 1, "page": 1, "page_size": 20, "items": [ ...job response... ] }`. History uses the same page envelope, with attempt number, status, video ID/URL, privacy status, upload/confirmation times, API error code, and details per item.

Truthful job states include `PENDING`, `SCHEDULED`, `UPLOADING`, `UPLOADED`, `PUBLISHED`, `FAILED`, `NEEDS_REVIEW`, and `CANCELLED`. `UPLOADED` means YouTube returned a video ID; only `PUBLISHED` means a later YouTube status check confirmed public visibility. Private and unlisted uploads remain `UPLOADED`. Retryable failures use exponential backoff and are bounded by `max_retries`. Quota errors wait until Google's Pacific-time daily reset. Network/5xx interruptions become `NEEDS_REVIEW`; inspect the channel or reconcile status before manual action.

Quota is distinct from the app's per-day schedule limit. YouTube upload operations can consume substantial quota; check the current Google quota table and project console. Common errors: `401` admin bearer missing/invalid; `403` missing rights or owner gate; `409` review/state/ambiguous outcome conflict; `429` backoff or quota exhaustion; `503` missing OAuth/encryption config or expired/revoked credentials; `502`/`504` provider/network failures.

### Scheduling, pause, and cancellation

`POST /api/v1/publishing/schedule` accepts `generated_short_ids` or selects eligible completed Shorts, plus `limit`, `privacy_status`, and `review_confirmed`. It creates idempotent jobs at slots within the configured local windows, weekdays, timezone, and daily target. It never schedules an unverified-rights, incomplete, failed, or unreviewed Short. `GET /api/v1/publishing/schedule` lists upcoming jobs.

Request:

```json
{
  "generated_short_ids": [42, 43, 44, 45, 46],
  "limit": 5,
  "privacy_status": "private",
  "review_confirmed": true
}
```

Response: `{ "count": 5, "jobs": [ ...publishing job response... ] }`. Use `GET /api/v1/publishing/schedule` to list them. It accepts `limit` (default 50, maximum 200).

```bash
curl -X POST http://localhost:8000/api/v1/publishing/schedule \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"generated_short_ids":[42,43,44,45,46],"limit":5,"privacy_status":"private","review_confirmed":true}'
curl 'http://localhost:8000/api/v1/publishing/schedule?limit=50' \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
```

For future YouTube publication, videos are uploaded with `privacyStatus=private` and `publishAt` shortly before their requested time. YouTube requires the video to remain private until the scheduled time and may require an eligible/verified channel. The API reports `SCHEDULED` after the private upload is confirmed; polling changes it to `PUBLISHED` only after YouTube reports public status. This behavior depends on YouTube API/account support.

`DELETE /api/v1/publishing/schedule/{job_id}` cancels a local not-yet-uploaded job; if already scheduled on YouTube, it calls YouTube and only records cancellation after private status is confirmed. `POST /api/v1/publishing/pause` stops new local uploads/scheduler work; `POST /api/v1/publishing/resume` resumes it. Pause does not cancel videos already scheduled on YouTube; cancel those jobs individually.

```bash
curl -X DELETE http://localhost:8000/api/v1/publishing/schedule/13 \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
curl -X POST http://localhost:8000/api/v1/publishing/pause \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
curl -X POST http://localhost:8000/api/v1/publishing/resume \
  -H "Authorization: Bearer $PUBLISHING_ADMIN_TOKEN"
```

The scheduler is a simple in-process loop started with FastAPI and persists jobs in SQLite. It is intentionally single-worker. It runs only while the app process is up: a stopped/suspended Codespace cannot publish. Codespace sleep, deletion, and storage are not a dependable always-on scheduler; use a continuously running owner-controlled host for production reliability. The owner can disable scheduler or pause globally at any time.

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

Tests cover Phase 1 health/errors and database migration; Phase 2 mocked discovery, metadata refresh/deduplication, filters, ranking and quota errors; Phase 3 rights/file/output validation, mock rendering, retries and safe paths; and Phase 4 OAuth state/encrypted tokens, admin protection, dry-run/privacy/review gates, resumable upload construction, ambiguous outcomes, quota/backoff, idempotency, timezone scheduling, daily caps, pause/cancel, and publication-state reconciliation. Google calls and video rendering are mocked, so tests require no Google credentials, uploads, source footage, FFmpeg, or eSpeak NG.

Phase 2 reuses `source_videos`, whose unique `platform_video_id` prevents duplicate candidates. Existing Phase 1 rows are preserved. `metric_history` stores a bounded set of timestamped observed view counts for velocity evaluation. No footage is downloaded or copied by any endpoint.