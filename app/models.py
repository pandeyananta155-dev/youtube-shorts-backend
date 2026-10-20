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
    review_status: Mapped[str] = mapped_column(String(32), default="NEEDS_REVIEW", index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_video: Mapped[SourceVideo] = relationship(back_populates="generated_shorts")
    publishing_jobs: Mapped[list["PublishingJob"]] = relationship(back_populates="generated_short")


class PublishingJob(TimestampMixin, Base):
    __tablename__ = "publishing_jobs"
    __table_args__ = (
        UniqueConstraint("generated_short_id", name="uq_publishing_generated_short"),
        UniqueConstraint("idempotency_key", name="uq_publishing_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    generated_short_id: Mapped[int] = mapped_column(ForeignKey("generated_shorts.id"), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    privacy_status: Mapped[str] = mapped_column(String(16), default="private")
    review_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    upload_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    api_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    video_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    title_override: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description_override: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    video_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    privacy_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    api_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    upload_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    publishing_job: Mapped[PublishingJob] = relationship(back_populates="upload_history")


class OAuthConnection(TimestampMixin, Base):
    __tablename__ = "oauth_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    channel_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="CONNECTED", index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublishingSettings(Base):
    __tablename__ = "publishing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    daily_target: Mapped[int] = mapped_column(Integer, default=5)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    publishing_windows: Mapped[list[dict]] = mapped_column(
        JSON, default=lambda: [{"start": "10:00", "end": "18:00"}]
    )
    allowed_days: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    globally_paused: Mapped[bool] = mapped_column(Boolean, default=True)
    require_review: Mapped[bool] = mapped_column(Boolean, default=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    public_publishing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_private_test_uploads: Mapped[bool] = mapped_column(Boolean, default=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    upload_lead_minutes: Mapped[int] = mapped_column(Integer, default=30)