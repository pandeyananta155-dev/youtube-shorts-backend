import logging
from threading import Lock
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.config import Settings
from app.generation.media import FFmpegMediaProcessor, MediaToolError
from app.models import (
    GeneratedShort,
    OAuthConnection,
    PublishingJob,
    PublishingSettings,
    SourceVideo,
    UploadHistory,
)
from app.publishing.oauth import OAuthNotConfigured, YouTubeOAuthService
from app.publishing.scheduler import count_daily_commitments, count_upload_attempts, publication_slots
from app.publishing.youtube_client import (
    PublishingProvider,
    UploadOutcomeUnknown,
    UploadResult,
    YouTubeApiError,
)

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_SCHEDULED = "SCHEDULED"
STATUS_UPLOADING = "UPLOADING"
STATUS_UPLOADED = "UPLOADED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_FAILED = "FAILED"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_CANCELLED = "CANCELLED"
ACTIVE_DAILY_STATUSES = (STATUS_PENDING, STATUS_SCHEDULED, STATUS_UPLOADING, STATUS_UPLOADED, STATUS_PUBLISHED)


def get_publishing_settings(session: Session, settings: Settings) -> PublishingSettings:
    row = session.get(PublishingSettings, 1)
    if row is None:
        row = PublishingSettings(
            id=1,
            daily_target=settings.publishing_daily_target,
            timezone=settings.publishing_timezone,
            publishing_windows=settings.publishing_windows,
            allowed_days=settings.publishing_allowed_days,
            scheduler_enabled=settings.publishing_scheduler_enabled,
            globally_paused=settings.publishing_paused_by_default,
            require_review=settings.publishing_require_review,
            dry_run=settings.publishing_dry_run,
            public_publishing_enabled=settings.enable_public_publishing,
            allow_private_test_uploads=settings.allow_private_test_uploads,
            max_retries=settings.publishing_max_retries,
            upload_lead_minutes=settings.publishing_upload_lead_minutes,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


class PublishingService:
    def __init__(
        self,
        settings: Settings,
        oauth_service: YouTubeOAuthService,
        provider: PublishingProvider,
        media_processor: FFmpegMediaProcessor,
    ):
        self.settings = settings
        self.oauth_service = oauth_service
        self.provider = provider
        self.media_processor = media_processor
        self._upload_lock = Lock()

    def process_job(self, session: Session, job: PublishingJob, *, scheduled: bool = False) -> PublishingJob:
        with self._upload_lock:
            return self._process_job_locked(session, job, scheduled=scheduled)

    def _process_job_locked(
        self,
        session: Session,
        job: PublishingJob,
        *,
        scheduled: bool,
    ) -> PublishingJob:
        job = session.get(PublishingJob, job.id)
        short = session.get(GeneratedShort, job.generated_short_id)
        source = session.get(SourceVideo, short.source_video_id)
        controls = get_publishing_settings(session, self.settings)
        if controls.globally_paused:
            job.last_error = "Publishing is globally paused"
            session.commit()
            return job
        if job.status in {STATUS_PUBLISHED, STATUS_UPLOADED, STATUS_CANCELLED}:
            return job
        if job.outcome_unknown:
            self.reconcile_unknown(session, job)
            return session.get(PublishingJob, job.id)
        if not self._eligible(short, source, require_review=controls.require_review):
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = "Short is incomplete, missing rights verification, or has not been approved"
            session.commit()
            return job
        if controls.dry_run:
            job.dry_run = True
            job.last_error = "Dry run: no YouTube upload was attempted"
            session.commit()
            return job
        if job.privacy_status == "public" and not controls.public_publishing_enabled:
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = "Public publishing is disabled by the owner"
            session.commit()
            return job
        if job.privacy_status in {"private", "unlisted"} and not controls.allow_private_test_uploads:
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = "Private/unlisted test uploads are disabled by the owner"
            session.commit()
            return job
        if controls.require_review and not job.review_confirmed:
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = "Owner review is required before upload"
            session.commit()
            return job
        limit_reference = job.scheduled_at or datetime.now(timezone.utc)
        if limit_reference.tzinfo is None:
            limit_reference = limit_reference.replace(tzinfo=timezone.utc)
        target_day = limit_reference.astimezone(ZoneInfo(controls.timezone)).date()
        if count_upload_attempts(session, controls.timezone, target_day) >= controls.daily_target:
            return self._defer_scheduled_job(session, job, controls)
        if not short.output_path:
            return self._fail(session, job, "Completed Short output path is missing", "media_missing", retryable=False)
        output_path = Path(short.output_path).resolve()
        output_root = self.settings.generation_output_directory.resolve()
        if not output_path.is_relative_to(output_root) or not output_path.is_file() or output_path.stat().st_size <= 0:
            return self._fail(session, job, "Completed Short output is missing or invalid", "media_invalid", retryable=False)
        try:
            media_info = self.media_processor.probe(output_path)
        except MediaToolError as exc:
            return self._fail(session, job, f"Completed Short failed media validation: {exc}", "media_invalid", retryable=False)
        expected_width = short.output_width or self.settings.video_width
        expected_height = short.output_height or self.settings.video_height
        if (
            not media_info.has_video
            or media_info.duration_seconds <= 0
            or media_info.duration_seconds > self.settings.max_short_duration_seconds + 1
            or media_info.width != expected_width
            or media_info.height != expected_height
        ):
            return self._fail(
                session,
                job,
                "Completed Short did not pass pre-upload media validation",
                "media_invalid",
                retryable=False,
            )

        try:
            credentials = self.oauth_service.credentials(session)
        except OAuthNotConfigured as exc:
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = str(exc)
            job.api_error_code = "reauth_required"
            session.commit()
            return job

        now = datetime.now(timezone.utc)
        job.status = STATUS_UPLOADING
        job.dry_run = False
        job.attempts = (job.attempts or 0) + 1
        job.last_attempt_at = now
        job.upload_started_at = now
        job.last_error = None
        history = UploadHistory(
            publishing_job_id=job.id,
            status=STATUS_UPLOADING,
            privacy_status=job.privacy_status,
            attempt_number=job.attempts,
            upload_started_at=now,
        )
        session.add(history)
        session.commit()

        try:
            result = self.provider.upload(
                credentials,
                output_path,
                title=job.title_override or short.title or "YouTube Short",
                description=job.description_override or short.description or "#Shorts",
                privacy_status=job.privacy_status,
                publish_at=job.scheduled_at,
            )
            self._record_upload_success(session, job, history, result)
        except UploadOutcomeUnknown as exc:
            job = session.get(PublishingJob, job.id)
            history = session.get(UploadHistory, history.id)
            job.status = STATUS_NEEDS_REVIEW
            job.outcome_unknown = True
            job.last_error = str(exc)
            job.api_error_code = "upload_outcome_unknown"
            history.status = STATUS_NEEDS_REVIEW
            history.outcome_unknown = True
            history.details = str(exc)[:2000]
            session.commit()
            logger.warning("Upload outcome unknown; automatic retry disabled", extra={"job_id": job.id})
        except YouTubeApiError as exc:
            job = session.get(PublishingJob, job.id)
            history = session.get(UploadHistory, history.id)
            job.api_error_code = exc.code
            history.api_error_code = exc.code
            history.status = STATUS_FAILED
            history.details = str(exc)[:2000]
            if exc.retryable and not exc.quota_exhausted and job.attempts < job.max_attempts:
                backoff_seconds = min(3600, 30 * (2 ** (job.attempts - 1)))
                job.status = STATUS_FAILED
                job.retry_after_seconds = backoff_seconds
                job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
            elif exc.quota_exhausted:
                pacific = ZoneInfo("America/Los_Angeles")
                local_now = datetime.now(pacific)
                next_reset = (local_now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                job.status = STATUS_FAILED
                job.next_attempt_at = next_reset.astimezone(timezone.utc)
                job.retry_after_seconds = max(
                    0,
                    int((job.next_attempt_at - datetime.now(timezone.utc)).total_seconds()),
                )
            else:
                job.status = STATUS_FAILED
                job.next_attempt_at = None
            job.last_error = str(exc)[:2000]
            session.commit()
            logger.warning("YouTube publish attempt failed", extra={"job_id": job.id, "error_code": exc.code})
        except Exception:
            session.rollback()
            job = session.get(PublishingJob, job.id)
            history = session.get(UploadHistory, history.id)
            job.status = STATUS_NEEDS_REVIEW
            job.outcome_unknown = True
            job.last_error = "Unexpected upload interruption; reconcile with the channel before retrying"
            job.api_error_code = "upload_outcome_unknown"
            history.status = STATUS_NEEDS_REVIEW
            history.outcome_unknown = True
            history.details = job.last_error
            session.commit()
            logger.exception("Unexpected upload exception; retry blocked", extra={"job_id": job.id})
        return session.get(PublishingJob, job.id)

    def reconcile_unknown(self, session: Session, job: PublishingJob) -> PublishingJob:
        if not job.outcome_unknown:
            return job
        if not job.video_id:
            job.status = STATUS_NEEDS_REVIEW
            job.last_error = (
                "Upload outcome is uncertain and YouTube returned no video ID. "
                "Inspect the channel's recent uploads before any manual retry."
            )
            session.commit()
            return job
        try:
            credentials = self.oauth_service.credentials(session)
            result = self.provider.video_status(credentials, job.video_id)
        except (OAuthNotConfigured, YouTubeApiError) as exc:
            job.last_error = str(exc)
            session.commit()
            return job
        if result:
            self._record_reconciled_result(session, job, result)
        return session.get(PublishingJob, job.id)

    def run_due_once(self, session: Session) -> int:
        controls = get_publishing_settings(session, self.settings)
        if not controls.scheduler_enabled or controls.globally_paused:
            return 0
        now = datetime.now(timezone.utc)
        upload_horizon = now + timedelta(minutes=controls.upload_lead_minutes)
        due_jobs = (
            session.query(PublishingJob)
            .filter(
                PublishingJob.status.in_([STATUS_PENDING, STATUS_SCHEDULED, STATUS_FAILED]),
                PublishingJob.attempts < PublishingJob.max_attempts,
                PublishingJob.scheduled_at.is_not(None),
                PublishingJob.scheduled_at <= upload_horizon,
                PublishingJob.outcome_unknown.is_(False),
            )
            .order_by(PublishingJob.scheduled_at.asc())
            .limit(50)
            .all()
        )
        count = 0
        for job in due_jobs:
            local_day = job.scheduled_at
            if local_day.tzinfo is None:
                local_day = local_day.replace(tzinfo=timezone.utc)
            day = local_day.astimezone(ZoneInfo(controls.timezone)).date()
            if count_upload_attempts(session, controls.timezone, day) >= controls.daily_target:
                self._defer_scheduled_job(session, job, controls)
                continue
            if job.status == STATUS_FAILED and job.next_attempt_at:
                retry_at = job.next_attempt_at
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if retry_at > now:
                    continue
            self.process_job(session, job, scheduled=True)
            count += 1
        return count

    def _defer_scheduled_job(
        self,
        session: Session,
        job: PublishingJob,
        controls: PublishingSettings,
    ) -> PublishingJob:
        now = datetime.now(timezone.utc)
        next_slots = publication_slots(
            daily_target=controls.daily_target,
            timezone_name=controls.timezone,
            allowed_days=controls.allowed_days,
            publishing_windows=controls.publishing_windows,
            start_after=now,
            days_ahead=30,
        )
        for slot in next_slots:
            day = slot.astimezone(ZoneInfo(controls.timezone)).date()
            if count_daily_commitments(session, controls, day) < controls.daily_target:
                job.scheduled_at = slot
                job.status = STATUS_SCHEDULED
                job.last_error = "Deferred because the configured daily upload limit was reached"
                session.commit()
                return job
        job.last_error = "Daily upload limit reached; no eligible publishing slot found in the next 30 days"
        job.status = STATUS_SCHEDULED
        session.commit()
        return job

    def sync_publication_statuses_once(self, session: Session) -> int:
        jobs = (
            session.query(PublishingJob)
            .filter(
                PublishingJob.status.in_([STATUS_UPLOADED, STATUS_SCHEDULED]),
                PublishingJob.video_id.is_not(None),
                PublishingJob.outcome_unknown.is_(False),
            )
            .order_by(PublishingJob.id.asc())
            .limit(50)
            .all()
        )
        updated = 0
        for job in jobs:
            try:
                credentials = self.oauth_service.credentials(session)
                result = self.provider.video_status(credentials, job.video_id)
            except (OAuthNotConfigured, YouTubeApiError) as exc:
                job.last_error = str(exc)
                session.commit()
                continue
            if result is None:
                continue
            job.privacy_status = result.privacy_status
            job.published_at = result.published_at
            if result.privacy_status == "public" and result.published_at:
                job.status = STATUS_PUBLISHED
            elif result.publish_at:
                job.status = STATUS_SCHEDULED
            else:
                job.status = STATUS_UPLOADED
            history = (
                session.query(UploadHistory)
                .filter(UploadHistory.publishing_job_id == job.id)
                .order_by(UploadHistory.id.desc())
                .first()
            )
            if history:
                history.status = job.status
                history.privacy_status = result.privacy_status
                history.confirmed_at = datetime.now(timezone.utc)
                history.details = result.upload_status
            job.last_error = None
            session.commit()
            updated += 1
        return updated

    @staticmethod
    def _eligible(short: GeneratedShort, source: SourceVideo, *, require_review: bool) -> bool:
        return (
            short.status == "COMPLETED"
            and source.rights_verified
            and source.rights_verification_status == "VERIFIED"
            and (not require_review or short.review_status == "APPROVED")
        )

    @staticmethod
    def _record_upload_success(
        session: Session,
        job: PublishingJob,
        history: UploadHistory,
        result: UploadResult,
    ) -> None:
        job.video_id = result.video_id
        job.video_url = f"https://www.youtube.com/watch?v={result.video_id}"
        job.privacy_status = result.privacy_status
        job.outcome_unknown = False
        job.api_error_code = None
        job.last_error = None
        job.retry_after_seconds = None
        job.next_attempt_at = None
        job.published_at = result.published_at
        scheduled_at = result.publish_at or job.scheduled_at
        job.scheduled_at = scheduled_at
        job.status = STATUS_SCHEDULED if scheduled_at else STATUS_UPLOADED
        history.platform_video_id = result.video_id
        history.video_url = job.video_url
        history.privacy_status = result.privacy_status
        history.status = job.status
        history.uploaded_at = datetime.now(timezone.utc)
        history.confirmed_at = history.uploaded_at
        history.details = result.upload_status
        history.outcome_unknown = False
        session.commit()

    @staticmethod
    def _record_reconciled_result(session: Session, job: PublishingJob, result: UploadResult) -> None:
        job.video_id = result.video_id
        job.video_url = f"https://www.youtube.com/watch?v={result.video_id}"
        job.privacy_status = result.privacy_status
        job.outcome_unknown = False
        job.published_at = result.published_at
        job.status = STATUS_SCHEDULED if result.publish_at else STATUS_UPLOADED
        job.last_error = None
        history = (
            session.query(UploadHistory)
            .filter(UploadHistory.publishing_job_id == job.id)
            .order_by(UploadHistory.id.desc())
            .first()
        )
        if history:
            history.platform_video_id = result.video_id
            history.video_url = job.video_url
            history.status = job.status
            history.privacy_status = result.privacy_status
            history.confirmed_at = datetime.now(timezone.utc)
            history.outcome_unknown = False
            history.details = "Reconciled by YouTube videos.list"
        session.commit()

    def _fail(
        self,
        session: Session,
        job: PublishingJob,
        message: str,
        code: str,
        *,
        retryable: bool,
    ) -> PublishingJob:
        controls = get_publishing_settings(session, self.settings)
        job.status = STATUS_FAILED
        job.last_error = message
        job.api_error_code = code
        if retryable and job.attempts < job.max_attempts:
            delay = min(3600, 30 * (2 ** max(0, job.attempts - 1)))
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            job.retry_after_seconds = delay
        session.commit()
        return job