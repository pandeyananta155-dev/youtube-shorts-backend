# API and Testing Guide

Base URL for local development: `http://localhost:8000`. Interactive OpenAPI/Swagger documentation is available at `/docs`; the generated OpenAPI document is at `/openapi.json`.

## Health

`GET /health` checks that the API can reach its configured database.

Example:

```bash
curl -i http://localhost:8000/health
```

Success (`200 OK`):

```json
{
  "status": "ok",
  "database": "ok"
}
```

## Errors

Errors use one JSON envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Not Found",
    "details": null
  }
}
```

- `404 not_found`: requested route does not exist.
- `422 validation_error`: request data failed validation; `details` contains Pydantic validation details.
- `503 http_error`: the health check could not reach SQLite; message is `Database unavailable`.
- `500 internal_server_error`: unexpected server error; details are not exposed to the client. The exception is recorded in structured server logs.

Phase 1 has no write endpoints yet, so request-body validation errors will be exercised as future endpoints are added. FastAPI's `/docs` remains available for trying the current endpoint.

## Run tests

From the repository root with the virtual environment activated:

```bash
pytest
```

The tests use isolated temporary SQLite files, verify `/health` and the four initial database tables, and check the standard 404 error body. They require no credentials or network access.

## Database tables

- `source_videos`: source identity, URL, descriptive metadata, and workflow status.
- `generated_shorts`: generated output metadata linked to a source video.
- `publishing_jobs`: queued/scheduled publishing work and attempt/error tracking.
- `upload_history`: records of individual upload outcomes associated with a publishing job.

All processing and publishing behavior is intentionally out of scope for Phase 1.