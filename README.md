# YouTube Shorts Automation Backend

Phase 1 foundation for a locally runnable, free YouTube Shorts automation backend. This repository currently provides the FastAPI service, SQLite persistence, initial workflow models, health endpoint, and mock-friendly foundation. It does not discover videos, generate videos, or upload content; those are future phases.

## Requirements

- Python 3.12 or newer
- FFmpeg available on `PATH` for future video-processing phases. Phase 1 does not invoke FFmpeg.
- No API keys, OAuth credentials, paid services, or external database are required.

## Setup

From the repository root in Codespaces or a local terminal:

```bash
python --version
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, activate the environment with `.venv\\Scripts\\Activate.ps1` instead. `.env` is ignored by Git; never put API keys or OAuth tokens in committed files.

## Initialize and run

Initialize the SQLite database explicitly:

```bash
python -m app.init_db
```

Start the development server on port 8000:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The application also initializes its tables on startup, so the server can be started without the explicit initialization step. In Codespaces, open port 8000 when prompted. Visit `/health` for service/database status and `/docs` for interactive Swagger documentation.

## Tests

```bash
pytest
```

Tests use temporary SQLite databases and do not require network access or credentials.

## Configuration

Settings are loaded from environment variables and the optional root `.env` file. See [.env.example](.env.example). The default database is `sqlite:///./data/shorts.db`; set `DATABASE_URL` to change it. The database directory is created automatically. Set `LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

## Project layout

```text
app/
	config.py          Environment-backed settings
	database.py        SQLAlchemy engine/session setup
	errors.py          Consistent API error responses
	init_db.py         Explicit database initialization command
	logging_config.py  Structured JSON logging
	main.py            FastAPI app factory and health endpoint
	models.py          Initial workflow persistence models
docs/
	api-testing.md     Endpoint, response, error, and test guide
tests/
	test_api.py        API and persistence smoke tests
```

## Scope and next phases

The initial tables are `SourceVideo`, `GeneratedShort`, `PublishingJob`, and `UploadHistory`. Phase 1 exposes only the health endpoint; mock services and the local database keep the foundation runnable without paid APIs. Future discovery, FFmpeg generation, and authorized YouTube OAuth/upload workflows are intentionally not implemented here.