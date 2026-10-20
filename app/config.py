from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    source_media_directory: Path = Path("./data/media")
    generation_output_directory: Path = Path("./data/outputs")
    max_source_file_mb: int = Field(default=500, ge=1, le=10_000)
    max_output_file_mb: int = Field(default=1000, ge=1, le=20_000)
    media_download_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    default_short_duration_seconds: int = Field(default=45, ge=1, le=180)
    max_short_duration_seconds: int = Field(default=180, ge=1, le=600)
    video_aspect_ratio: str = "9:16"
    video_width: int = Field(default=1080, ge=144, le=3840)
    video_height: int = Field(default=1920, ge=256, le=3840)
    video_fps: int = Field(default=30, ge=1, le=60)
    video_crf: int = Field(default=23, ge=0, le=51)
    video_preset: str = Field(default="medium", pattern=r"^(ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow)$")
    audio_bitrate: str = Field(default="192k", pattern=r"^(96|128|160|192|256|320)k$")
    caption_font_name: str = "DejaVu Sans"
    caption_font_size: int = Field(default=48, ge=12, le=120)
    caption_margin_vertical: int = Field(default=120, ge=0, le=600)
    caption_outline_size: int = Field(default=3, ge=0, le=10)
    caption_primary_color: str = Field(default="&H00FFFFFF", pattern=r"^&H[0-9A-Fa-f]{8}$")
    caption_outline_color: str = Field(default="&H00000000", pattern=r"^&H[0-9A-Fa-f]{8}$")
    narration_provider: Literal["none", "espeak"] = "none"
    narration_voice: str = "en"
    narration_speed: int = Field(default=165, ge=80, le=300)
    espeak_binary: str = "espeak-ng"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    max_generation_attempts: int = Field(default=3, ge=1, le=10)
    category_templates: dict[str, str] = Field(default_factory=dict)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/youtube/auth/callback"
    oauth_token_encryption_key: SecretStr | None = None
    publishing_admin_token: SecretStr | None = None
    publishing_dry_run: bool = True
    enable_public_publishing: bool = False
    allow_private_test_uploads: bool = False
    publishing_daily_target: int = Field(default=5, ge=5, le=7)
    publishing_timezone: str = "UTC"
    publishing_windows: list[dict[str, str]] = Field(
        default_factory=lambda: [{"start": "10:00", "end": "18:00"}]
    )
    publishing_allowed_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    publishing_scheduler_enabled: bool = False
    publishing_paused_by_default: bool = True
    publishing_require_review: bool = True
    publishing_scheduler_interval_seconds: int = Field(default=60, ge=10, le=3600)
    publishing_max_retries: int = Field(default=3, ge=1, le=10)
    publishing_upload_lead_minutes: int = Field(default=30, ge=5, le=1440)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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
        if self.default_short_duration_seconds > self.max_short_duration_seconds:
            raise ValueError("Default short duration cannot exceed maximum duration")
        ratio_parts = self.video_aspect_ratio.split(":")
        if len(ratio_parts) != 2:
            raise ValueError("VIDEO_ASPECT_RATIO must use width:height notation")
        try:
            expected_ratio = int(ratio_parts[0]) / int(ratio_parts[1])
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("VIDEO_ASPECT_RATIO must contain positive integers") from exc
        if expected_ratio <= 0 or abs((self.video_width / self.video_height) - expected_ratio) > 0.01:
            raise ValueError("Configured video dimensions must match VIDEO_ASPECT_RATIO")
        if any(day < 0 or day > 6 for day in self.publishing_allowed_days):
            raise ValueError("PUBLISHING_ALLOWED_DAYS must contain weekday numbers from 0 to 6")
        if self.publishing_admin_token and len(self.publishing_admin_token.get_secret_value()) < 32:
            raise ValueError("PUBLISHING_ADMIN_TOKEN must contain at least 32 characters")
        return self

    @field_validator("caption_font_name")
    @classmethod
    def validate_font_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9 _-]{1,64}", value.strip()):
            raise ValueError("Caption font name contains unsupported characters")
        return value.strip()

    @field_validator("narration_voice")
    @classmethod
    def validate_voice_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,32}", value.strip()):
            raise ValueError("Narration voice contains unsupported characters")
        return value.strip()

    @property
    def supported_categories(self) -> list[str]:
        return [item.strip().lower() for item in self.supported_categories_csv.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()