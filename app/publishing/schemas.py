from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class OAuthStatusResponse(BaseModel):
    connected: bool
    status: str
    channel_title: str | None = None
    connected_at: datetime | None = None
    last_verified_at: datetime | None = None


class PublishingJobCreate(BaseModel):
    generated_short_id: int = Field(ge=1)
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    review_confirmed: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)

    model_config = {
        "json_schema_extra": {
            "example": {
                "generated_short_id": 42,
                "privacy_status": "private",
                "review_confirmed": True,
                "idempotency_key": "owner-short-42-review-1",
                "title": "Reviewed Short title",
                "description": "Reviewed description and #Shorts",
            }
        }
    }


class ScheduleRequest(BaseModel):
    generated_short_ids: list[int] | None = Field(default=None, max_length=100)
    limit: int = Field(default=7, ge=1, le=50)
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    review_confirmed: bool = False

    @field_validator("generated_short_ids")
    @classmethod
    def normalize_ids(cls, values: list[int] | None) -> list[int] | None:
        if values is None:
            return None
        unique = list(dict.fromkeys(values))
        if not unique or any(value < 1 for value in unique):
            raise ValueError("generated_short_ids must contain positive IDs")
        return unique


class PublishingWindow(BaseModel):
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_order(self) -> "PublishingWindow":
        if self.start >= self.end:
            raise ValueError("Publishing window start must be earlier than end on the same day")
        return self


class PublishingSettingsUpdate(BaseModel):
    daily_target: int = Field(ge=5, le=7)
    timezone: str = Field(min_length=1, max_length=64)
    publishing_windows: list[PublishingWindow] = Field(min_length=1, max_length=8)
    allowed_days: list[int] = Field(min_length=1, max_length=7)
    scheduler_enabled: bool
    globally_paused: bool
    require_review: bool
    dry_run: bool
    public_publishing_enabled: bool
    allow_private_test_uploads: bool
    max_retries: int = Field(ge=1, le=10)
    upload_lead_minutes: int = Field(ge=5, le=1440)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("allowed_days")
    @classmethod
    def validate_days(cls, values: list[int]) -> list[int]:
        if any(value < 0 or value > 6 for value in values):
            raise ValueError("allowed_days must use weekday numbers 0 (Monday) through 6 (Sunday)")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_windows_do_not_overlap(self) -> "PublishingSettingsUpdate":
        windows = sorted(self.publishing_windows, key=lambda item: item.start)
        for previous, current in zip(windows, windows[1:]):
            if previous.end > current.start:
                raise ValueError("Publishing windows cannot overlap")
        if self.public_publishing_enabled and self.dry_run:
            raise ValueError("Disable dry_run explicitly before enabling public publishing")
        return self


class PublishingSettingsResponse(PublishingSettingsUpdate):
    pass


class PublishingJobResponse(BaseModel):
    job_id: int
    generated_short_id: int
    title: str | None
    description: str | None
    status: str
    privacy_status: str
    dry_run: bool
    review_confirmed: bool
    scheduled_at: datetime | None
    video_id: str | None
    video_url: str | None
    attempts: int
    max_attempts: int
    last_error: str | None
    api_error_code: str | None
    next_attempt_at: datetime | None
    outcome_unknown: bool
    created_at: datetime
    updated_at: datetime


class PublishingJobPageResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PublishingJobResponse]


class ScheduleResponse(BaseModel):
    count: int
    jobs: list[PublishingJobResponse]


class PublishingHistoryItem(BaseModel):
    history_id: int
    job_id: int
    generated_short_id: int
    status: str
    video_id: str | None
    video_url: str | None
    privacy_status: str | None
    attempt_number: int
    uploaded_at: datetime | None
    confirmed_at: datetime | None
    error_code: str | None
    details: str | None


class PublishingHistoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PublishingHistoryItem]