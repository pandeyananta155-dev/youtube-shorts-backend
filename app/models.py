from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceVideo(TimestampMixin, Base):
    __tablename__ = "source_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    platform_video_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(2048))
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ranking_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    ranking_factors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    metric_history: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True, default=list)
    source_type: Mapped[str] = mapped_column(String(32), default="youtube_candidate", index=True)
    source_creator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_or_permission: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rights_verification_status: Mapped[str] = mapped_column(
        String(32), default="REQUIRES_PERMISSION", index=True
    )
    local_source_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    asset_source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    generated_shorts: Mapped[list["GeneratedShort"]] = relationship(back_populates="source_video")


class GeneratedShort(TimestampMixin, Base):
    __tablename__ = "generated_shorts"
    __table_args__ = (
        UniqueConstraint("source_video_id", "config_hash", name="uq_generated_source_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_video_id: Mapped[int] = mapped_column(ForeignKey("source_videos.id"), index=True)
    output_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    captions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_file_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    narration_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    render_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_video: Mapped[SourceVideo] = relationship(back_populates="generated_shorts")
    publishing_jobs: Mapped[list["PublishingJob"]] = relationship(back_populates="generated_short")


class PublishingJob(TimestampMixin, Base):
    __tablename__ = "publishing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    generated_short_id: Mapped[int] = mapped_column(ForeignKey("generated_shorts.id"), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_short: Mapped[GeneratedShort] = relationship(back_populates="publishing_jobs")
    upload_history: Mapped[list["UploadHistory"]] = relationship(back_populates="publishing_job")


class UploadHistory(TimestampMixin, Base):
    __tablename__ = "upload_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    publishing_job_id: Mapped[int] = mapped_column(ForeignKey("publishing_jobs.id"), index=True)
    platform_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    publishing_job: Mapped[PublishingJob] = relationship(back_populates="upload_history")