import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.generation.media import FFmpegMediaProcessor, MediaToolError
from app.generation.permissions import (
    SUPPORTED_VIDEO_EXTENSIONS,
    SourceValidationError,
    ensure_within_directory,
)
from app.generation.providers import (
    CaptionProvider,
    EspeakNarrationProvider,
    ScriptProvider,
    TemplateCaptionProvider,
    TemplateScriptProvider,
)
from app.models import GeneratedShort, SourceVideo

logger = logging.getLogger(__name__)

JOB_PENDING = "PENDING"
JOB_VALIDATING = "VALIDATING_SOURCE"
JOB_SCRIPT = "GENERATING_SCRIPT"
JOB_RENDERING = "RENDERING"
JOB_COMPLETED = "COMPLETED"
JOB_PERMISSION = "REQUIRES_PERMISSION"
JOB_FAILED = "FAILED"


class GenerationService:
    def __init__(
        self,
        settings: Settings,
        media_processor: FFmpegMediaProcessor,
        script_provider: ScriptProvider | None = None,
        caption_provider: CaptionProvider | None = None,
        narration_provider=None,
    ):
        self.settings = settings
        self.media_processor = media_processor
        self.script_provider = script_provider or TemplateScriptProvider(settings.category_templates)
        self.caption_provider = caption_provider or TemplateCaptionProvider()
        self.narration_provider = narration_provider

    def process(self, session: Session, job: GeneratedShort, source: SourceVideo) -> GeneratedShort:
        options = job.render_settings or {}
        job.attempt_count = (job.attempt_count or 0) + 1
        job.started_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.error_message = None
        job.status = JOB_PENDING
        session.commit()

        if not source.rights_verified or not source.license_or_permission:
            source.rights_verification_status = JOB_PERMISSION
            source.status = JOB_PERMISSION
            job.status = JOB_PERMISSION
            job.error_message = "Verified permission or a reuse license is required before media can be rendered."
            session.commit()
            return job

        output_path = self._output_path(job.id)
        temporary_output = output_path.with_name(f"render_{job.id}_{job.attempt_count}.partial.mp4")
        caption_path = self.settings.generation_output_directory / f"captions_{job.id}.srt"
        narration_path = self.settings.generation_output_directory / f"narration_{job.id}_{job.attempt_count}.wav"
        try:
            job.status = JOB_VALIDATING
            session.commit()
            source_path = self._validate_source(source)
            source_info = self.media_processor.probe(source_path)
            if not source_info.has_video or source_info.duration_seconds <= 0:
                raise SourceValidationError("Source must contain a valid video stream and duration")

            requested_duration = int(options.get("duration_seconds") or self.settings.default_short_duration_seconds)
            if requested_duration > self.settings.max_short_duration_seconds:
                raise SourceValidationError("Requested duration exceeds the configured maximum")
            render_duration = max(1, min(requested_duration, math.floor(source_info.duration_seconds)))

            job.status = JOB_SCRIPT
            session.commit()
            title = (options.get("title") or source.title or "Untitled Short").strip()[:100]
            category = (options.get("category") or source.category or "").strip().lower() or None
            script = options.get("script")
            if not script:
                script = self.script_provider.generate(
                    title=title,
                    commentary=options.get("commentary"),
                    category=category,
                )
            script = script.strip()
            if not script:
                raise ValueError("Script provider returned an empty script")
            if category == "children":
                self._validate_child_content(script)
            captions = options.get("captions")
            if not captions:
                captions = self.caption_provider.generate(script, render_duration)
            captions = re.sub(r"\{[^}]*\}|<[^>]*>", "", captions)
            if not captions.strip():
                raise ValueError("Caption provider returned empty captions")
            if category == "children":
                self._validate_child_content(captions)
            caption_path.parent.mkdir(parents=True, exist_ok=True)
            caption_path.write_text(captions, encoding="utf-8")

            job.script = script
            job.captions_text = captions
            job.caption_file_path = str(caption_path)
            job.title = title
            category_tag = re.sub(r"[^A-Za-z0-9]", "", category or "")[:30]
            hashtags = "#Shorts" + (f" #{category_tag}" if category_tag else "")
            job.description = f"{script[:1500]}\n\n{hashtags}"
            job.render_settings = {
                **options,
                "duration_seconds": render_duration,
                "width": self.settings.video_width,
                "height": self.settings.video_height,
                "aspect_ratio": self.settings.video_aspect_ratio,
                "fps": self.settings.video_fps,
                "caption_font_name": self.settings.caption_font_name,
                "caption_font_size": self.settings.caption_font_size,
                "caption_margin_vertical": self.settings.caption_margin_vertical,
                "caption_outline_size": self.settings.caption_outline_size,
                "caption_primary_color": self.settings.caption_primary_color,
                "caption_outline_color": self.settings.caption_outline_color,
                "video_crf": self.settings.video_crf,
                "video_preset": self.settings.video_preset,
                "audio_bitrate": self.settings.audio_bitrate,
            }
            session.commit()

            narration_metadata = None
            if options.get("include_narration"):
                if self.settings.narration_provider == "none":
                    raise RuntimeError(
                        "Narration was requested but NARRATION_PROVIDER is set to none. "
                        "Set it to espeak and install eSpeak NG, or disable narration."
                    )
                if self.settings.narration_provider == "espeak":
                    narration_provider = self.narration_provider or EspeakNarrationProvider(self.settings)
                    narration_metadata = narration_provider.generate(script, narration_path)
                else:
                    raise RuntimeError("Configured narration provider is not available")

            job.status = JOB_RENDERING
            job.narration_metadata = narration_metadata
            session.commit()

            self.media_processor.render(
                source_path=source_path,
                output_path=temporary_output,
                caption_path=caption_path,
                duration_seconds=render_duration,
                narration_path=narration_path if narration_metadata else None,
            )
            if not temporary_output.is_file():
                raise MediaToolError("FFmpeg completed without producing an output file")
            output_size = temporary_output.stat().st_size
            if output_size <= 0:
                raise MediaToolError("Rendered output file is empty")
            if output_size > self.settings.max_output_file_mb * 1024 * 1024:
                raise MediaToolError("Rendered output exceeds MAX_OUTPUT_FILE_MB")
            output_info = self.media_processor.probe(temporary_output)
            if (
                output_info.width != self.settings.video_width
                or output_info.height != self.settings.video_height
                or output_info.duration_seconds <= 0
                or output_info.duration_seconds > self.settings.max_short_duration_seconds + 1
            ):
                raise MediaToolError("Rendered output did not pass resolution/duration validation")
            temporary_output.replace(output_path)

            job.output_path = str(output_path)
            job.duration_seconds = round(output_info.duration_seconds)
            job.output_width = output_info.width
            job.output_height = output_info.height
            job.output_fps = self.settings.video_fps
            job.file_size_bytes = output_size
            job.status = JOB_COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = None
            source.status = "short_generated"
            session.commit()
            logger.info("Short generation completed", extra={"job_id": job.id, "source_id": source.id})
        except Exception as exc:
            session.rollback()
            job = session.get(GeneratedShort, job.id)
            source = session.get(SourceVideo, source.id)
            job.status = JOB_FAILED
            job.error_message = str(exc)[:2000]
            source.status = "generation_failed"
            session.commit()
            logger.exception("Short generation failed", extra={"job_id": job.id, "source_id": source.id})
        finally:
            temporary_output.unlink(missing_ok=True)
            narration_path.unlink(missing_ok=True)
        session.refresh(job)
        return job

    def _validate_source(self, source: SourceVideo) -> Path:
        if not source.local_source_path:
            raise SourceValidationError(
                "No local authorized media is attached. Upload an authorized file or register an authorized direct video URL."
            )
        source_path = ensure_within_directory(
            Path(source.local_source_path), self.settings.source_media_directory
        )
        if not source_path.is_file():
            raise SourceValidationError("Authorized source media file is missing from local storage")
        if source_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise SourceValidationError("Source file extension is not supported")
        if source_path.stat().st_size > self.settings.max_source_file_mb * 1024 * 1024:
            raise SourceValidationError("Source video exceeds MAX_SOURCE_FILE_MB")
        return source_path

    def _output_path(self, job_id: int) -> Path:
        output_directory = self.settings.generation_output_directory.resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        candidate = output_directory / f"short_{job_id}.mp4"
        return ensure_within_directory(candidate, output_directory)

    @staticmethod
    def _validate_child_content(text: str) -> None:
        email_pattern = r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"
        phone_pattern = r"\b\+?\d[\d\s().-]{8,}\d\b"
        if re.search(email_pattern, text) or re.search(phone_pattern, text):
            raise ValueError("Children's content drafts must not contain personal contact information")