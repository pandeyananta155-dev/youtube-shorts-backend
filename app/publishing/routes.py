import hmac
import logging
from datetime import datetime, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings
from app.discovery.routes import get_session
from app.models import (
    GeneratedShort,
    OAuthConnection,
    PublishingJob,
    PublishingSettings,
    SourceVideo,
    UploadHistory,
)
from app.publishing.oauth import OAuthNotConfigured
from app.publishing.oauth import OAuthNotConfigured, OAuthStateError
from app.publishing.scheduler import count_daily_commitments, publication_slots
from app.publishing.schemas import (
    OAuthStatusResponse,
    PublishingHistoryItem,
    PublishingHistoryResponse,
    PublishingJobCreate,
    PublishingJobPageResponse,
    PublishingJobResponse,
    PublishingSettingsResponse,
    PublishingSettingsUpdate,
    ScheduleRequest,
    ScheduleResponse,
)
from app.publishing.service import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    STATUS_UPLOADED,
    PublishingService,
    get_publishing_settings,
)
from app.publishing.youtube_client import YouTubeApiError

router = APIRouter(prefix="/api/v1", tags=["YouTube publishing"])
logger = logging.getLogger(__name__)


def require_publishing_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings: Settings = request.app.state.settings
    expected = (
        settings.publishing_admin_token.get_secret_value()
        if settings.publishing_admin_token
        else None
    )
    if not expected:
        raise HTTPException(status_code=503, detail="PUBLISHING_ADMIN_TOKEN is not configured")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid owner bearer token required")


Admin = Depends(require_publishing_admin)


def _job_response(job: PublishingJob) -> PublishingJobResponse:
    short = job.generated_short
    return PublishingJobResponse(
        job_id=job.id,
        generated_short_id=job.generated_short_id,
        title=job.title_override or short.title,
        description=job.description_override or short.description,
        status=job.status,
        privacy_status=job.privacy_status,
        dry_run=job.dry_run,
        review_confirmed=job.review_confirmed,
        scheduled_at=job.scheduled_at,
        video_id=job.video_id,
        video_url=job.video_url,
        attempts=job.attempts or 0,
        max_attempts=job.max_attempts or 3,
        last_error=job.last_error,
        api_error_code=job.api_error_code,
        next_attempt_at=job.next_attempt_at,
        outcome_unknown=job.outcome_unknown,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _settings_response(row: PublishingSettings) -> PublishingSettingsResponse:
    return PublishingSettingsResponse(
        daily_target=row.daily_target,
        timezone=row.timezone,
        publishing_windows=row.publishing_windows,
        allowed_days=row.allowed_days,
        scheduler_enabled=row.scheduler_enabled,
        globally_paused=row.globally_paused,
        require_review=row.require_review,
        dry_run=row.dry_run,
        public_publishing_enabled=row.public_publishing_enabled,
        allow_private_test_uploads=row.allow_private_test_uploads,
        max_retries=row.max_retries,
        upload_lead_minutes=row.upload_lead_minutes,
    )


def _check_publishable_source(session: Session, short: GeneratedShort) -> SourceVideo:
    if short.status != "COMPLETED" or not short.output_path:
        raise HTTPException(status_code=409, detail="Only completed Phase 3 Shorts can be published")
    source = session.get(SourceVideo, short.source_video_id)
    if not source or not source.rights_verified or source.rights_verification_status != "VERIFIED":
        raise HTTPException(status_code=403, detail="Verified source reuse rights are required")
    if short.review_status in {"FAILED", "REQUIRES_PERMISSION"}:
        raise HTTPException(status_code=409, detail=f"Short is not publishable: {short.review_status}")
    return source


@router.get("/youtube/auth/status", response_model=OAuthStatusResponse)
def auth_status(session: Session = Depends(get_session)) -> OAuthStatusResponse:
    connection = session.get(OAuthConnection, 1)
    if not connection:
        return OAuthStatusResponse(connected=False, status="DISCONNECTED")
    return OAuthStatusResponse(
        connected=connection.status == "CONNECTED",
        status=connection.status,
        channel_title=connection.channel_title,
        connected_at=connection.connected_at,
        last_verified_at=connection.last_verified_at,
    )


@router.get("/youtube/auth/start")
def auth_start(
    request: Request,
    session: Session = Depends(get_session),
    _: None = Admin,
) -> dict[str, object]:
    try:
        url = request.app.state.oauth_service.authorization_url(session)
    except (OAuthNotConfigured, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"authorization_url": url, "state_expires_in_seconds": 600}


@router.get("/youtube/auth/callback", response_model=OAuthStatusResponse)
def auth_callback(
    request: Request,
    state: str = Query(min_length=1, max_length=256),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=256),
    session: Session = Depends(get_session),
) -> OAuthStatusResponse:
    if error:
        raise HTTPException(status_code=400, detail="Google authorization was denied or cancelled")
    if not code:
        raise HTTPException(status_code=400, detail="Google authorization code is missing")
    try:
        connection = request.app.state.oauth_service.complete_callback(
            session,
            state,
            str(request.url),
        )
    except OAuthStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OAuthNotConfigured, RuntimeError) as exc:
        logger.warning("OAuth callback could not be completed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OAuthStatusResponse(
        connected=True,
        status=connection.status,
        channel_title=connection.channel_title,
        connected_at=connection.connected_at,
        last_verified_at=connection.last_verified_at,
    )


@router.post("/youtube/auth/disconnect", dependencies=[Admin])
def auth_disconnect(request: Request, session: Session = Depends(get_session)) -> dict[str, str]:
    request.app.state.oauth_service.disconnect(session)
    return {"status": "DISCONNECTED"}


@router.post("/publishing/jobs", response_model=PublishingJobResponse, status_code=201, dependencies=[Admin])
def create_publishing_job(
    body: PublishingJobCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> PublishingJobResponse:
    short = session.get(GeneratedShort, body.generated_short_id)
    if short is None:
        raise HTTPException(status_code=404, detail="Generated Short not found")
    source = _check_publishable_source(session, short)
    controls = get_publishing_settings(session, request.app.state.settings)
    if body.review_confirmed:
        short.review_status = "APPROVED"
        short.reviewed_at = datetime.now(timezone.utc)
        session.commit()
    existing = (
        session.query(PublishingJob)
        .filter(PublishingJob.generated_short_id == short.id)
        .one_or_none()
    )
    if body.idempotency_key:
        same_key = session.query(PublishingJob).filter_by(idempotency_key=body.idempotency_key).one_or_none()
        if same_key and same_key.generated_short_id != short.id:
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different Short")
        existing = existing or same_key
    if existing:
        if existing.status == STATUS_NEEDS_REVIEW and body.review_confirmed:
            short.review_status = "APPROVED"
            short.reviewed_at = datetime.now(timezone.utc)
            existing.review_confirmed = True
            existing.title_override = body.title or existing.title_override
            existing.description_override = body.description or existing.description_override
            existing.privacy_status = body.privacy_status
            existing.dry_run = controls.dry_run
            existing.max_attempts = controls.max_retries
            existing.last_error = None
            if body.privacy_status == "public" and not controls.public_publishing_enabled:
                existing.last_error = "Public publishing is disabled by the owner"
            elif (
                not controls.dry_run
                and body.privacy_status in {"private", "unlisted"}
                and not controls.allow_private_test_uploads
            ):
                existing.last_error = "Private/unlisted test uploads are disabled by the owner"
            else:
                existing.status = STATUS_PENDING
            session.commit()
            if existing.status == STATUS_PENDING:
                existing = request.app.state.publishing_service.process_job(session, existing)
            return _job_response(existing)
        return _job_response(existing)

    status = STATUS_PENDING
    error = None
    if controls.require_review and not body.review_confirmed and short.review_status != "APPROVED":
        status = STATUS_NEEDS_REVIEW
        error = "Owner review is required before publishing"
    if body.privacy_status == "public" and not controls.public_publishing_enabled:
        status = STATUS_NEEDS_REVIEW
        error = "Public publishing is disabled by the owner"
    if not controls.dry_run and body.privacy_status in {"private", "unlisted"} and not controls.allow_private_test_uploads:
        status = STATUS_NEEDS_REVIEW
        error = "Private/unlisted test uploads are disabled by the owner"
    job = PublishingJob(
        generated_short_id=short.id,
        idempotency_key=body.idempotency_key,
        privacy_status=body.privacy_status,
        review_confirmed=body.review_confirmed or short.review_status == "APPROVED",
        dry_run=controls.dry_run,
        max_attempts=controls.max_retries,
        status=status,
        last_error=error,
        title_override=body.title,
        description_override=body.description,
    )
    session.add(job)
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = session.query(PublishingJob).filter_by(generated_short_id=short.id).one_or_none()
        if existing:
            return _job_response(existing)
        raise
    session.refresh(job)
    if job.status == STATUS_PENDING:
        job = request.app.state.publishing_service.process_job(session, job)
    return _job_response(job)


@router.get("/publishing/jobs", response_model=PublishingJobPageResponse, dependencies=[Admin])
def list_publishing_jobs(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None, max_length=32),
    privacy_status: str | None = Query(default=None, pattern="^(private|unlisted|public)$"),
) -> PublishingJobPageResponse:
    query = session.query(PublishingJob)
    if status:
        query = query.filter(PublishingJob.status == status.upper())
    if privacy_status:
        query = query.filter(PublishingJob.privacy_status == privacy_status)
    total = query.with_entities(func.count(PublishingJob.id)).scalar() or 0
    jobs = query.order_by(PublishingJob.created_at.desc(), PublishingJob.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PublishingJobPageResponse(total=total, page=page, page_size=page_size, items=[_job_response(job) for job in jobs])


@router.get("/publishing/jobs/{job_id}", response_model=PublishingJobResponse, dependencies=[Admin])
def get_publishing_job(job_id: int, session: Session = Depends(get_session)) -> PublishingJobResponse:
    job = session.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Publishing job not found")
    return _job_response(job)


@router.post("/publishing/jobs/{job_id}/retry", response_model=PublishingJobResponse, dependencies=[Admin])
def retry_publishing_job(job_id: int, request: Request, session: Session = Depends(get_session)) -> PublishingJobResponse:
    job = session.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Publishing job not found")
    if job.outcome_unknown:
        job = request.app.state.publishing_service.reconcile_unknown(session, job)
        if job.outcome_unknown or not job.video_id:
            raise HTTPException(status_code=409, detail="Upload outcome is unknown; reconcile with the channel before retrying")
        return _job_response(job)
    controls = get_publishing_settings(session, request.app.state.settings)
    dry_run_promotion = job.status == STATUS_PENDING and job.dry_run and not controls.dry_run
    if job.status != STATUS_FAILED and not dry_run_promotion:
        raise HTTPException(status_code=409, detail="Only a known failed job or explicit dry-run promotion may be retried")
    if job.attempts >= job.max_attempts:
        raise HTTPException(status_code=409, detail="Maximum publishing attempts reached")
    if job.next_attempt_at:
        retry_at = job.next_attempt_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if retry_at > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail="Retry backoff has not elapsed")
    job.status = STATUS_PENDING
    job.dry_run = controls.dry_run
    job.next_attempt_at = None
    job = request.app.state.publishing_service.process_job(session, job)
    return _job_response(job)


@router.post("/publishing/schedule", response_model=ScheduleResponse, dependencies=[Admin])
def schedule_publishing(
    body: ScheduleRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ScheduleResponse:
    settings: Settings = request.app.state.settings
    controls = get_publishing_settings(session, settings)
    if body.privacy_status == "public" and not controls.public_publishing_enabled:
        raise HTTPException(status_code=409, detail="Public publishing is disabled by the owner")
    if (
        not controls.dry_run
        and body.privacy_status in {"private", "unlisted"}
        and not controls.allow_private_test_uploads
    ):
        raise HTTPException(status_code=409, detail="Private/unlisted test uploads are disabled by the owner")
    if controls.require_review and not body.review_confirmed:
        raise HTTPException(status_code=409, detail="Owner review confirmation is required for scheduling")
    if body.generated_short_ids:
        shorts = (
            session.query(GeneratedShort)
            .filter(GeneratedShort.id.in_(body.generated_short_ids))
            .order_by(GeneratedShort.completed_at.asc(), GeneratedShort.id.asc())
            .all()
        )
        if len(shorts) != len(body.generated_short_ids):
            raise HTTPException(status_code=404, detail="One or more generated Shorts were not found")
    else:
        shorts = (
            session.query(GeneratedShort)
            .filter(GeneratedShort.status == "COMPLETED")
            .order_by(GeneratedShort.completed_at.asc(), GeneratedShort.id.asc())
            .all()
        )
    eligible = []
    for short in shorts:
        source = session.get(SourceVideo, short.source_video_id)
        if short.status != "COMPLETED" or not short.output_path or not source or not source.rights_verified or source.rights_verification_status != "VERIFIED":
            continue
        if controls.require_review and short.review_status != "APPROVED":
            if body.review_confirmed:
                short.review_status = "APPROVED"
                short.reviewed_at = datetime.now(timezone.utc)
            else:
                continue
        eligible.append(short)
    if not eligible:
        return ScheduleResponse(count=0, jobs=[])

    now = datetime.now(timezone.utc)
    slots = publication_slots(
        daily_target=controls.daily_target,
        timezone_name=controls.timezone,
        allowed_days=controls.allowed_days,
        publishing_windows=controls.publishing_windows,
        start_after=now,
        days_ahead=30,
    )
    current_counts: dict[object, int] = {}
    planned = []
    for short in eligible:
        if len(planned) >= body.limit:
            break
        existing = session.query(PublishingJob).filter_by(generated_short_id=short.id).one_or_none()
        if existing and existing.status != STATUS_CANCELLED:
            continue
        chosen_slot = None
        for slot in slots:
            local_day = slot.astimezone(ZoneInfo(controls.timezone)).date()
            if local_day not in current_counts:
                current_counts[local_day] = count_daily_commitments(session, controls, local_day)
            if current_counts[local_day] < controls.daily_target:
                chosen_slot = slot
                current_counts[local_day] += 1
                break
        if chosen_slot is None:
            break
        if existing:
            existing.scheduled_at = chosen_slot
            existing.status = STATUS_SCHEDULED
            existing.privacy_status = body.privacy_status
            existing.review_confirmed = body.review_confirmed or short.review_status == "APPROVED"
            existing.dry_run = controls.dry_run
            existing.max_attempts = controls.max_retries
            existing.cancelled_at = None
            existing.last_error = None
            job = existing
        else:
            job = PublishingJob(
                generated_short_id=short.id,
                scheduled_at=chosen_slot,
                status=STATUS_SCHEDULED,
                privacy_status=body.privacy_status,
                review_confirmed=body.review_confirmed or short.review_status == "APPROVED",
                dry_run=controls.dry_run,
                max_attempts=controls.max_retries,
                idempotency_key=f"scheduled-short-{short.id}",
            )
            session.add(job)
        planned.append(job)
    session.commit()
    for job in planned:
        session.refresh(job)
    return ScheduleResponse(count=len(planned), jobs=[_job_response(job) for job in planned])


@router.get("/publishing/schedule", response_model=ScheduleResponse, dependencies=[Admin])
def get_publishing_schedule(
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> ScheduleResponse:
    jobs = (
        session.query(PublishingJob)
        .filter(PublishingJob.status == STATUS_SCHEDULED)
        .order_by(PublishingJob.scheduled_at.asc())
        .limit(limit)
        .all()
    )
    return ScheduleResponse(count=len(jobs), jobs=[_job_response(job) for job in jobs])


@router.delete("/publishing/schedule/{job_id}", dependencies=[Admin])
def cancel_scheduled_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    job = session.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Publishing job not found")
    if job.status != STATUS_SCHEDULED:
        raise HTTPException(status_code=409, detail="Only a scheduled publishing job can be cancelled")
    if job.video_id:
        try:
            credentials = request.app.state.oauth_service.credentials(session)
            cancelled = request.app.state.publishing_provider.cancel_scheduled(credentials, job.video_id)
        except (OAuthNotConfigured, YouTubeApiError) as exc:
            raise HTTPException(status_code=503, detail=f"Could not cancel YouTube-side schedule: {exc}") from exc
        if not cancelled:
            raise HTTPException(status_code=502, detail="YouTube did not confirm that the scheduled video is private")
    job.status = STATUS_CANCELLED
    job.cancelled_at = datetime.now(timezone.utc)
    session.commit()
    return {"status": STATUS_CANCELLED}


@router.get("/publishing/history", response_model=PublishingHistoryResponse, dependencies=[Admin])
def publishing_history(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None, max_length=32),
) -> PublishingHistoryResponse:
    query = session.query(UploadHistory)
    if status:
        query = query.filter(UploadHistory.status == status.upper())
    total = query.with_entities(func.count(UploadHistory.id)).scalar() or 0
    rows = query.order_by(UploadHistory.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        PublishingHistoryItem(
            history_id=row.id,
            job_id=row.publishing_job_id,
            generated_short_id=row.publishing_job.generated_short_id,
            status=row.status,
            video_id=row.platform_video_id,
            video_url=row.video_url,
            privacy_status=row.privacy_status,
            attempt_number=row.attempt_number,
            uploaded_at=row.uploaded_at,
            confirmed_at=row.confirmed_at,
            error_code=row.api_error_code,
            details=row.details,
        )
        for row in rows
    ]
    return PublishingHistoryResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/publishing/settings", response_model=PublishingSettingsResponse, dependencies=[Admin])
def read_publishing_settings(
    request: Request,
    session: Session = Depends(get_session),
) -> PublishingSettingsResponse:
    return _settings_response(get_publishing_settings(session, request.app.state.settings))


@router.put("/publishing/settings", response_model=PublishingSettingsResponse, dependencies=[Admin])
def update_publishing_settings(
    body: PublishingSettingsUpdate,
    request: Request,
    session: Session = Depends(get_session),
) -> PublishingSettingsResponse:
    row = get_publishing_settings(session, request.app.state.settings)
    for field, value in body.model_dump().items():
        if field == "publishing_windows":
            value = [window.model_dump() if hasattr(window, "model_dump") else window for window in value]
        setattr(row, field, value)
    session.commit()
    session.refresh(row)
    return _settings_response(row)


@router.post("/publishing/pause", dependencies=[Admin])
def pause_publishing(request: Request, session: Session = Depends(get_session)) -> dict[str, bool]:
    row = get_publishing_settings(session, request.app.state.settings)
    row.globally_paused = True
    session.commit()
    logger.warning("Publishing globally paused")
    return {"globally_paused": True}


@router.post("/publishing/resume", dependencies=[Admin])
def resume_publishing(request: Request, session: Session = Depends(get_session)) -> dict[str, bool]:
    row = get_publishing_settings(session, request.app.state.settings)
    row.globally_paused = False
    session.commit()
    logger.warning("Publishing globally resumed")
    return {"globally_paused": False}