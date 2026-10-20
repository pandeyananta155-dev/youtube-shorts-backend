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
    }
    with engine.begin() as connection:
        for column_name, column_type in additive_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE source_videos ADD COLUMN {column_name} {column_type}")
                )