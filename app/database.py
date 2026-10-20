from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_database_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    if database_url.startswith("sqlite:///./"):
        database_path = Path(database_url.removeprefix("sqlite:///./"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, connect_args=connect_args, future=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_schema(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    existing_columns = {column["name"] for column in inspect(engine).get_columns("source_videos")}
    additive_columns = {
        "published_at": "DATETIME",
        "view_count": "INTEGER",
        "like_count": "INTEGER",
        "category": "VARCHAR(80)",
        "source_query": "VARCHAR(500)",
        "discovered_at": "DATETIME",
        "metrics_updated_at": "DATETIME",
        "ranking_score": "FLOAT",
        "ranking_factors": "JSON",
        "evaluated_at": "DATETIME",
        "country_code": "VARCHAR(2)",
        "language_code": "VARCHAR(10)",
        "metric_history": "JSON",
        "source_type": "VARCHAR(32) NOT NULL DEFAULT 'youtube_candidate'",
        "source_creator": "VARCHAR(255)",
        "license_or_permission": "TEXT",
        "rights_verified": "BOOLEAN NOT NULL DEFAULT 0",
        "rights_verification_status": "VARCHAR(32) NOT NULL DEFAULT 'REQUIRES_PERMISSION'",
        "local_source_path": "VARCHAR(2048)",
        "asset_source_url": "VARCHAR(2048)",
    }
    with engine.begin() as connection:
        for column_name, column_type in additive_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE source_videos ADD COLUMN {column_name} {column_type}")
                )
        short_columns = {column["name"] for column in inspect(connection).get_columns("generated_shorts")}
        short_additive_columns = {
            "config_hash": "VARCHAR(64)",
            "script": "TEXT",
            "captions_text": "TEXT",
            "caption_file_path": "VARCHAR(2048)",
            "narration_metadata": "JSON",
            "render_settings": "JSON",
            "output_width": "INTEGER",
            "output_height": "INTEGER",
            "output_fps": "INTEGER",
            "file_size_bytes": "INTEGER",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "error_message": "TEXT",
        }
        for column_name, column_type in short_additive_columns.items():
            if column_name not in short_columns:
                connection.execute(
                    text(f"ALTER TABLE generated_shorts ADD COLUMN {column_name} {column_type}")
                )
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_generated_source_config "
            "ON generated_shorts (source_video_id, config_hash)"
        ))
        generated_columns = {
            "review_status": "VARCHAR(32) NOT NULL DEFAULT 'NEEDS_REVIEW'",
            "reviewed_at": "DATETIME",
        }
        generated_existing = {column["name"] for column in inspect(connection).get_columns("generated_shorts")}
        for column_name, column_type in generated_columns.items():
            if column_name not in generated_existing:
                connection.execute(text(f"ALTER TABLE generated_shorts ADD COLUMN {column_name} {column_type}"))

        publishing_columns = {
            "idempotency_key": "VARCHAR(128)",
            "privacy_status": "VARCHAR(16) NOT NULL DEFAULT 'private'",
            "review_confirmed": "BOOLEAN NOT NULL DEFAULT 0",
            "dry_run": "BOOLEAN NOT NULL DEFAULT 1",
            "upload_started_at": "DATETIME",
            "last_attempt_at": "DATETIME",
            "next_attempt_at": "DATETIME",
            "outcome_unknown": "BOOLEAN NOT NULL DEFAULT 0",
            "api_error_code": "VARCHAR(128)",
            "video_id": "VARCHAR(128)",
            "video_url": "VARCHAR(512)",
            "published_at": "DATETIME",
            "cancelled_at": "DATETIME",
            "retry_after_seconds": "INTEGER",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "title_override": "VARCHAR(100)",
            "description_override": "TEXT",
        }
        publishing_existing = {column["name"] for column in inspect(connection).get_columns("publishing_jobs")}
        for column_name, column_type in publishing_columns.items():
            if column_name not in publishing_existing:
                connection.execute(text(f"ALTER TABLE publishing_jobs ADD COLUMN {column_name} {column_type}"))

        upload_columns = {
            "video_url": "VARCHAR(512)",
            "privacy_status": "VARCHAR(16)",
            "api_error_code": "VARCHAR(128)",
            "attempt_number": "INTEGER NOT NULL DEFAULT 1",
            "upload_started_at": "DATETIME",
            "confirmed_at": "DATETIME",
            "outcome_unknown": "BOOLEAN NOT NULL DEFAULT 0",
        }
        upload_existing = {column["name"] for column in inspect(connection).get_columns("upload_history")}
        for column_name, column_type in upload_columns.items():
            if column_name not in upload_existing:
                connection.execute(text(f"ALTER TABLE upload_history ADD COLUMN {column_name} {column_type}"))

        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_publishing_generated_short "
            "ON publishing_jobs (generated_short_id)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_publishing_idempotency_key "
            "ON publishing_jobs (idempotency_key)"
        ))
        publishing_setting_columns = {
            column["name"] for column in inspect(connection).get_columns("publishing_settings")
        }
        setting_additions = {
            "dry_run": "BOOLEAN NOT NULL DEFAULT 1",
            "public_publishing_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "allow_private_test_uploads": "BOOLEAN NOT NULL DEFAULT 0",
            "max_retries": "INTEGER NOT NULL DEFAULT 3",
            "upload_lead_minutes": "INTEGER NOT NULL DEFAULT 30",
        }
        for column_name, column_type in setting_additions.items():
            if column_name not in publishing_setting_columns:
                connection.execute(
                    text(f"ALTER TABLE publishing_settings ADD COLUMN {column_name} {column_type}")
                )