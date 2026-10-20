from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CATEGORIES = (
    "celebrities,music,rap,documentaries,animation,gaming,education,comedy,sports,children"
)


class Settings(BaseSettings):
    app_name: str = "YouTube Shorts Automation Backend"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/shorts.db"
    youtube_api_key: SecretStr | None = None
    youtube_request_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    youtube_max_queries_per_request: int = Field(default=5, ge=1, le=100)
    supported_categories_csv: str = DEFAULT_CATEGORIES
    ranking_recency_weight: float = Field(default=0.20, ge=0)
    ranking_views_weight: float = Field(default=0.45, ge=0)
    ranking_engagement_weight: float = Field(default=0.25, ge=0)
    ranking_velocity_weight: float = Field(default=0.10, ge=0)
    ranking_recency_half_life_days: float = Field(default=7.0, gt=0)
    ranking_views_per_day_reference: float = Field(default=100_000.0, gt=0)
    ranking_engagement_reference: float = Field(default=0.05, gt=0)
    ranking_min_velocity_interval_hours: float = Field(default=1.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_ranking_weights(self) -> "Settings":
        weights = (
            self.ranking_recency_weight,
            self.ranking_views_weight,
            self.ranking_engagement_weight,
            self.ranking_velocity_weight,
        )
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError("Ranking weights must be non-negative with a positive total")
        return self

    @property
    def supported_categories(self) -> list[str]:
        return [item.strip().lower() for item in self.supported_categories_csv.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()