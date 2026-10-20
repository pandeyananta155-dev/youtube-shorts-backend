import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.database import create_database_engine, create_session_factory, ensure_schema
from app.discovery.provider import DiscoveryProvider, YouTubeDataApiProvider
from app.discovery.routes import router as discovery_router
from app.errors import register_error_handlers
from app.generation.media import FFmpegMediaProcessor
from app.generation.routes import router as generation_router
from app.generation.service import GenerationService
from app.logging_config import configure_logging
from app import models  # noqa: F401 - register model metadata before table creation

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    discovery_provider: DiscoveryProvider | None = None,
    media_processor: FFmpegMediaProcessor | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    app_engine = engine or create_database_engine(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        ensure_schema(app_engine)
        logger.info("Application started", extra={"environment": app_settings.environment})
        yield
        app_engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        description="Local YouTube Shorts discovery, ranking, and authorized video generation backend.",
        lifespan=lifespan,
    )
    app.state.engine = app_engine
    app.state.session_factory = create_session_factory(app_engine)
    app.state.settings = app_settings
    api_key = (
        app_settings.youtube_api_key.get_secret_value()
        if app_settings.youtube_api_key
        else None
    )
    app.state.discovery_provider = discovery_provider or YouTubeDataApiProvider(
        api_key,
        timeout_seconds=app_settings.youtube_request_timeout_seconds,
    )
    app.state.media_processor = media_processor or FFmpegMediaProcessor(app_settings)
    app.state.generation_service = GenerationService(app_settings, app.state.media_processor)
    register_error_handlers(app)
    app.include_router(discovery_router)
    app.include_router(generation_router)

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