from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import create_database_engine
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