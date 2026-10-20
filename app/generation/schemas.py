from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class GenerateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": 17,
                "title": "How this optical illusion works",
                "category": "education",
                "commentary": "Here is an original explanation of the visual effect.",
                "duration_seconds": 30,
                "include_narration": False,
            }
        }
    )

    source_id: int | None = Field(default=None, ge=1)
    candidate_video_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=80)
    commentary: str | None = Field(default=None, max_length=4000)
    script: str | None = Field(default=None, max_length=4000)
    captions: str | None = Field(default=None, max_length=12000)
    duration_seconds: int | None = Field(default=None, ge=1, le=600)
    include_narration: bool = False

    @model_validator(mode="after")
    def validate_source_selector(self) -> "GenerateRequest":
        if (self.source_id is None) == (self.candidate_video_id is None):
            raise ValueError("Provide exactly one of source_id or candidate_video_id")
        if self.script and self.commentary:
            raise ValueError("Provide script or commentary, not both")
        return self


class AuthorizedUrlSourceRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_url": "https://media.example.org/owned-clip.mp4",
                "creator": "My production team",
                "license_or_permission": "Owned by our production team",
                "rights_verified": True,
                "category": "education",
            }
        }
    )

    source_url: HttpUrl
    creator: str = Field(min_length=1, max_length=255)
    license_or_permission: str = Field(min_length=5, max_length=2000)
    rights_verified: bool = False
    candidate_video_id: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=80)


class SourceResponse(BaseModel):
    source_id: int
    candidate_video_id: str
    title: str
    source_type: str
    rights_verification_status: str
    rights_verified: bool
    creator: str | None
    media_attached: bool


class GenerationJobResponse(BaseModel):
    job_id: int
    source_id: int
    candidate_video_id: str
    status: str
    attempt_count: int
    error: str | None
    draft_title: str | None
    draft_description: str | None
    script: str | None
    captions: str | None
    narration: dict[str, Any] | None
    duration_seconds: int | None
    width: int | None
    height: int | None
    fps: int | None
    file_size_bytes: int | None
    output_filename: str | None
    preview_url: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class GenerationJobPageResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[GenerationJobResponse]


class GenerationSettingsResponse(BaseModel):
    default_duration_seconds: int
    maximum_duration_seconds: int
    aspect_ratio: str
    width: int
    height: int
    fps: int
    video_crf: int
    video_preset: str
    audio_bitrate: str
    caption_font_name: str
    caption_font_size: int
    caption_margin_vertical: int
    caption_outline_size: int
    caption_primary_color: str
    caption_outline_color: str
    narration_provider: str
    narration_available: bool
    ffmpeg_available: bool
    ffprobe_available: bool
    max_source_file_mb: int
    max_output_file_mb: int
    output_directory: str
    category_templates: dict[str, str]