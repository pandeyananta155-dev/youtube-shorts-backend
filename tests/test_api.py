from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import create_database_engine, ensure_schema
from app.main import create_app


def test_health_reports_database_status(tmp_path):
    database_url = make_url(f"sqlite:///{tmp_path / 'health.db'}")
    engine = create_database_engine(str(database_url))
    app = create_app(Settings(database_url=str(database_url)), engine=engine)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert set(inspect(engine).get_table_names()) == {
        "source_videos",
        "generated_shorts",
        "publishing_jobs",
        "upload_history",
    }


def test_unknown_route_uses_consistent_error_response(tmp_path):
    database_url = make_url(f"sqlite:///{tmp_path / 'errors.db'}")
    engine = create_database_engine(str(database_url))
    app = create_app(Settings(database_url=str(database_url)), engine=engine)

    with TestClient(app) as client:
        response = client.get("/not-a-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "details": None,
        }
    }


def test_phase_one_database_gets_additive_discovery_columns(tmp_path):
    database_url = make_url(f"sqlite:///{tmp_path / 'legacy.db'}")
    engine = create_database_engine(str(database_url))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE source_videos ("
                "id INTEGER PRIMARY KEY, platform_video_id VARCHAR(128) NOT NULL UNIQUE, "
                "title VARCHAR(500) NOT NULL, source_url VARCHAR(2048) NOT NULL, "
                "channel_name VARCHAR(255), duration_seconds INTEGER, status VARCHAR(32) NOT NULL, "
                "metadata_json TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE generated_shorts ("
                "id INTEGER PRIMARY KEY, source_video_id INTEGER NOT NULL, output_path VARCHAR(2048), "
                "title VARCHAR(100), description TEXT, duration_seconds INTEGER, status VARCHAR(32) NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO source_videos "
                "(platform_video_id, title, source_url, status, created_at, updated_at) "
                "VALUES ('existing001', 'Existing source', 'https://example.test/video', "
                "'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    ensure_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("source_videos")}
    short_columns = {column["name"] for column in inspect(engine).get_columns("generated_shorts")}
    with engine.connect() as connection:
        existing_title = connection.execute(
            text("SELECT title FROM source_videos WHERE platform_video_id = 'existing001'")
        ).scalar_one()
    assert {"category", "view_count", "ranking_score", "metric_history"}.issubset(columns)
    assert {"config_hash", "script", "captions_text", "attempt_count", "error_message"}.issubset(short_columns)
    assert existing_title == "Existing source"