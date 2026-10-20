from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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

    generated_shorts: Mapped[list["GeneratedShort"]] = relationship(back_populates="source_video")


class GeneratedShort(TimestampMixin, Base):
    __tablename__ = "generated_shorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_video_id: Mapped[int] = mapped_column(ForeignKey("source_videos.id"), index=True)
    output_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

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