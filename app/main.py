import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.database import Base, create_database_engine, create_session_factory
from app.errors import register_error_handlers
from app.logging_config import configure_logging
from app import models  # noqa: F401 - register model metadata before table creation

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    app_engine = engine or create_database_engine(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        Base.metadata.create_all(bind=app_engine)
        logger.info("Application started", extra={"environment": app_settings.environment})
        yield
        app_engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        description="Phase 1 local foundation for YouTube Shorts automation.",
        lifespan=lifespan,
    )
    app.state.engine = app_engine
    app.state.session_factory = create_session_factory(app_engine)
    register_error_handlers(app)

    @app.get("/health", tags=["health"], summary="Check service and database health")
    def health() -> dict[str, str]:
        try:
            with app_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            logger.exception("Database health check failed")
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "database": "ok"}

    return app


app = create_app()