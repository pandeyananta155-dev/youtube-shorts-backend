import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Iterator
from uuid import uuid4
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.discovery.routes import get_session
from app.generation.media import MediaToolError
from app.generation.permissions import (
    SUPPORTED_VIDEO_EXTENSIONS,
    AuthorizedUrlFetcher,
    SourceValidationError,
    ensure_within_directory,
)
from app.generation.schemas import (
    AuthorizedUrlSourceRequest,
    GenerateRequest,
    GenerationJobPageResponse,
    GenerationJobResponse,
    GenerationSettingsResponse,
    SourceResponse,
)
from app.generation.service import JOB_COMPLETED, JOB_FAILED, JOB_PERMISSION
from app.models import GeneratedShort, SourceVideo

router = APIRouter(prefix="/api/v1/shorts", tags=["short generation"])
logger = logging.getLogger(__name__)


def _source_response(source: SourceVideo) -> SourceResponse:
    return SourceResponse(
        source_id=source.id,
        candidate_video_id=source.platform_video_id,
        title=source.title,
        source_type=source.source_type,
        rights_verification_status=source.rights_verification_status,
        rights_verified=source.rights_verified,
        creator=source.source_creator,
        media_attached=bool(source.local_source_path),
    )


def _job_response(job: GeneratedShort) -> GenerationJobResponse:
    source = job.source_video
    completed = job.status == JOB_COMPLETED
    return GenerationJobResponse(
        job_id=job.id,
        source_id=job.source_video_id,
        candidate_video_id=source.platform_video_id,
        status=job.status,
        attempt_count=job.attempt_count or 0,
        error=job.error_message,
        draft_title=job.title,
        draft_description=job.description,
        script=job.script,
        captions=job.captions_text,
        narration=job.narration_metadata,
        duration_seconds=job.duration_seconds,
        width=job.output_width,
        height=job.output_height,
        fps=job.output_fps,
        file_size_bytes=job.file_size_bytes,
        output_filename=Path(job.output_path).name if completed and job.output_path else None,
        preview_url=f"/api/v1/shorts/jobs/{job.id}/preview" if completed else None,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _require_permission(verified: bool, details: str | None) -> None:
    if not verified:
        raise HTTPException(
            status_code=403,
            detail="Verified ownership or reuse permission is required before storing source media.",
        )
    if not details or len(details.strip()) < 5:
        raise HTTPException(status_code=422, detail="Provide license or permission details (at least 5 characters).")


def _candidate_source(session: Session, candidate_video_id: str | None) -> SourceVideo | None:
    if candidate_video_id is None:
        return None
    source = (
        session.query(SourceVideo)
        .filter(SourceVideo.platform_video_id == candidate_video_id)
        .one_or_none()
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Discovered candidate not found")
    return source


def _persist_media_source(
    session: Session,
    settings: Settings,
    staged_path: Path,
    extension: str,
    source_type: str,
    permission_details: str,
    creator: str,
    candidate: SourceVideo | None,
    asset_source_url: str | None,
    category: str | None = None,
    source_title: str | None = None,
) -> SourceVideo:
    media_root = settings.source_media_directory.resolve()
    final_path = media_root / f"source_{uuid4().hex}{extension}"
    final_path = ensure_within_directory(final_path, media_root)
    staged_path.replace(final_path)
    if candidate is None:
        source_key = f"local_{uuid4().hex}"
        source = SourceVideo(
            platform_video_id=source_key,
                title=(source_title or final_path.stem)[:500],
            source_url=f"local-media://{source_key}",
            status="authorized_source",
        )
        session.add(source)
        session.flush()
    else:
        source = candidate
    source.source_type = source_type
    source.source_creator = creator.strip()
    source.license_or_permission = permission_details.strip()
    source.rights_verified = True
    source.rights_verification_status = "VERIFIED"
    source.local_source_path = str(final_path)
    source.asset_source_url = asset_source_url
    if category:
        source.category = category.strip().lower()
    if candidate:
        source.status = "authorized_source"
    try:
        session.commit()
    except Exception:
        session.rollback()
        final_path.unlink(missing_ok=True)
        raise
    session.refresh(source)
    return source


async def _save_upload(upload: UploadFile, settings: Settings) -> tuple[Path, str]:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Supported video formats are MP4, MOV, M4V, MKV, and WebM.")
    content_type = (upload.content_type or "").split(";", 1)[0].lower()
    if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Uploaded file must have a video content type.")
    media_root = settings.source_media_directory.resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    staged_path = media_root / f"upload_{uuid4().hex}.part"
    max_bytes = settings.max_source_file_mb * 1024 * 1024
    total_bytes = 0
    try:
        with staged_path.open("xb") as destination:
            while chunk := await upload.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="Source file exceeds MAX_SOURCE_FILE_MB.")
                destination.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded source file is empty.")
        return staged_path, extension
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.post(
    "/sources/upload",
    response_model=SourceResponse,
    status_code=201,
    summary="Upload authorized local source video",
    description="Uploads only media you own or are licensed to reuse. The rights fields are user attestations, not automated legal checks.",
)
async def upload_authorized_source(
    request: Request,
    file: UploadFile = File(..., description="Authorized MP4, MOV, M4V, MKV, or WebM file"),
    creator: str = Form(..., min_length=1, max_length=255, description="Creator or rights holder"),
    license_or_permission: str = Form(..., min_length=5, max_length=2000, description="License or permission details"),
    rights_verified: bool = Form(..., description="Set true only after verifying reuse rights"),
    candidate_video_id: str | None = Form(default=None, max_length=128, description="Optional Phase 2 YouTube candidate ID"),
    category: str | None = Form(default=None, max_length=80),
    session: Session = Depends(get_session),
) -> SourceResponse:
    _require_permission(rights_verified, license_or_permission)
    candidate = _candidate_source(session, candidate_video_id)
    settings: Settings = request.app.state.settings
    staged_path, extension = await _save_upload(file, settings)
    try:
        info = await run_in_threadpool(request.app.state.media_processor.probe, staged_path)
        if not info.has_video or info.duration_seconds <= 0:
            raise HTTPException(status_code=415, detail="Uploaded file has no valid video stream.")
        source = _persist_media_source(
            session,
            settings,
            staged_path,
            extension,
            "user_file",
            license_or_permission,
            creator,
            candidate,
            asset_source_url=None,
            category=category,
            source_title=Path(file.filename or "source video").stem,
        )
    except MediaToolError as exc:
        staged_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Cannot validate uploaded video: {exc}") from exc
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise
    logger.info("Authorized source file registered", extra={"source_id": source.id, "source_type": "user_file"})
    return _source_response(source)


@router.post(
    "/sources/url",
    response_model=SourceResponse,
    status_code=201,
    summary="Register an authorized direct media URL",
    description="Downloads a direct public HTTPS video only after rights attestation. YouTube URLs, redirects, and private IPs are blocked.",
)
def register_authorized_url(
    body: AuthorizedUrlSourceRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> SourceResponse:
    _require_permission(body.rights_verified, body.license_or_permission)
    candidate = _candidate_source(session, body.candidate_video_id)
    settings: Settings = request.app.state.settings
    fetcher = AuthorizedUrlFetcher(
        max_bytes=settings.max_source_file_mb * 1024 * 1024,
        timeout_seconds=settings.media_download_timeout_seconds,
    )
    staged_path = None
    try:
        staged_path, content_type = fetcher.download(
            str(body.source_url),
            settings.source_media_directory.resolve(),
        )
        info = request.app.state.media_processor.probe(staged_path)
        if not info.has_video or info.duration_seconds <= 0:
            raise SourceValidationError("Downloaded asset has no valid video stream")
        extension = staged_path.suffix.lower()
        title = Path(unquote(urlsplit(str(body.source_url)).path)).stem
        source = _persist_media_source(
            session,
            settings,
            staged_path,
            extension,
            "authorized_url",
            body.license_or_permission,
            body.creator,
            candidate,
            asset_source_url=str(body.source_url),
            category=body.category,
            source_title=title,
        )
        if not candidate:
            source.title = title
            session.commit()
    except SourceValidationError as exc:
        if staged_path:
            staged_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MediaToolError as exc:
        if staged_path:
            staged_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Cannot validate video URL: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Authorized media URL timed out") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Could not fetch authorized media URL") from exc
    logger.info("Authorized source URL registered", extra={"source_id": source.id, "source_type": "authorized_url"})
    return _source_response(source)


def _generation_config(body: GenerateRequest, settings: Settings) -> dict:
    options = body.model_dump(exclude={"source_id", "candidate_video_id"}, exclude_none=True)
    options["duration_seconds"] = body.duration_seconds or settings.default_short_duration_seconds
    return options


def _config_hash(source_id: int, options: dict, settings: Settings) -> str:
    signature = {
        "source_id": source_id,
        "options": options,
        "width": settings.video_width,
        "height": settings.video_height,
        "fps": settings.video_fps,
        "caption_font": settings.caption_font_name,
        "caption_size": settings.caption_font_size,
        "caption_margin": settings.caption_margin_vertical,
        "caption_primary_color": settings.caption_primary_color,
        "caption_outline_color": settings.caption_outline_color,
        "caption_outline_size": settings.caption_outline_size,
        "video_crf": settings.video_crf,
        "video_preset": settings.video_preset,
        "audio_bitrate": settings.audio_bitrate,
        "narration_provider": settings.narration_provider,
        "narration_voice": settings.narration_voice,
        "narration_speed": settings.narration_speed,
        "category_templates": settings.category_templates,
    }
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()


def _run_job(request: Request, session: Session, job: GeneratedShort, source: SourceVideo) -> GeneratedShort:
    return request.app.state.generation_service.process(session, job, source)


@router.post(
    "/generate",
    response_model=GenerationJobResponse,
    status_code=201,
    summary="Generate a vertical Short from authorized media",
    description="Runs synchronously, returns a persisted job, and reuses an existing job for an identical source/configuration.",
)
def generate_short(
    body: GenerateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> GenerationJobResponse:
    if body.source_id is not None:
        source = session.get(SourceVideo, body.source_id)
    else:
        source = (
            session.query(SourceVideo)
            .filter(SourceVideo.platform_video_id == body.candidate_video_id)
            .one_or_none()
        )
    if source is None:
        raise HTTPException(status_code=404, detail="Source or discovered candidate not found")

    settings: Settings = request.app.state.settings
    options = _generation_config(body, settings)
    if options["duration_seconds"] > settings.max_short_duration_seconds:
        raise HTTPException(status_code=422, detail="Requested duration exceeds MAX_SHORT_DURATION_SECONDS")
    config_hash = _config_hash(source.id, options, settings)
    job = (
        session.query(GeneratedShort)
        .filter(
            GeneratedShort.source_video_id == source.id,
            GeneratedShort.config_hash == config_hash,
        )
        .one_or_none()
    )
    if job is None:
        job = GeneratedShort(
            source_video_id=source.id,
            config_hash=config_hash,
            render_settings=options,
            status="PENDING",
            attempt_count=0,
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            job = (
                session.query(GeneratedShort)
                .filter(
                    GeneratedShort.source_video_id == source.id,
                    GeneratedShort.config_hash == config_hash,
                )
                .one()
            )
    if job.status in {JOB_COMPLETED, JOB_FAILED}:
        return _job_response(job)
    if job.status == JOB_PERMISSION and not (source.rights_verified and source.local_source_path):
        return _job_response(job)
    job = _run_job(request, session, job, source)
    return _job_response(job)


@router.get("/jobs", response_model=GenerationJobPageResponse)
def list_generation_jobs(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None, max_length=32),
    source_id: int | None = Query(default=None, ge=1),
) -> GenerationJobPageResponse:
    query = session.query(GeneratedShort)
    if status:
        query = query.filter(GeneratedShort.status == status.upper())
    if source_id:
        query = query.filter(GeneratedShort.source_video_id == source_id)
    total = query.with_entities(func.count(GeneratedShort.id)).scalar() or 0
    jobs = (
        query.order_by(GeneratedShort.created_at.desc(), GeneratedShort.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return GenerationJobPageResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_job_response(job) for job in jobs],
    )


@router.get("/jobs/{job_id}", response_model=GenerationJobResponse)
def get_generation_job(
    job_id: int,
    session: Session = Depends(get_session),
) -> GenerationJobResponse:
    job = session.get(GeneratedShort, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return _job_response(job)


@router.post("/jobs/{job_id}/retry", response_model=GenerationJobResponse)
def retry_generation_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> GenerationJobResponse:
    job = session.get(GeneratedShort, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    source = session.get(SourceVideo, job.source_video_id)
    settings: Settings = request.app.state.settings
    can_retry_permission_job = (
        job.status == JOB_PERMISSION and source.rights_verified and bool(source.local_source_path)
    )
    if job.status != JOB_FAILED and not can_retry_permission_job:
        raise HTTPException(status_code=409, detail="Only failed jobs or newly authorized permission-blocked jobs can be retried")
    if (job.attempt_count or 0) >= settings.max_generation_attempts:
        raise HTTPException(status_code=409, detail="Maximum generation attempts reached")
    job = _run_job(request, session, job, source)
    return _job_response(job)


@router.get(
    "/jobs/{job_id}/preview",
    summary="Preview or download the completed MP4",
    description="Streams only a completed output inside the configured output directory; no filesystem path is accepted.",
)
def preview_generation(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> FileResponse:
    job = session.get(GeneratedShort, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.status != JOB_COMPLETED or not job.output_path:
        raise HTTPException(status_code=409, detail="Generation output is not available")
    settings: Settings = request.app.state.settings
    try:
        path = ensure_within_directory(Path(job.output_path), settings.generation_output_directory)
    except SourceValidationError as exc:
        logger.error("Rejected output path outside configured directory", extra={"job_id": job.id})
        raise HTTPException(status_code=404, detail="Generation output not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generation output file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/settings", response_model=GenerationSettingsResponse)
def generation_settings(request: Request) -> GenerationSettingsResponse:
    settings: Settings = request.app.state.settings
    return GenerationSettingsResponse(
        default_duration_seconds=settings.default_short_duration_seconds,
        maximum_duration_seconds=settings.max_short_duration_seconds,
        aspect_ratio=settings.video_aspect_ratio,
        width=settings.video_width,
        height=settings.video_height,
        fps=settings.video_fps,
        video_crf=settings.video_crf,
        video_preset=settings.video_preset,
        audio_bitrate=settings.audio_bitrate,
        caption_font_name=settings.caption_font_name,
        caption_font_size=settings.caption_font_size,
        caption_margin_vertical=settings.caption_margin_vertical,
        caption_outline_size=settings.caption_outline_size,
        caption_primary_color=settings.caption_primary_color,
        caption_outline_color=settings.caption_outline_color,
        narration_provider=settings.narration_provider,
        narration_available=(
            settings.narration_provider == "espeak" and bool(shutil.which(settings.espeak_binary))
        ),
        ffmpeg_available=bool(shutil.which(settings.ffmpeg_binary)),
        ffprobe_available=bool(shutil.which(settings.ffprobe_binary)),
        max_source_file_mb=settings.max_source_file_mb,
        max_output_file_mb=settings.max_output_file_mb,
        output_directory=settings.generation_output_directory.name or "outputs",
        category_templates=settings.category_templates,
    )