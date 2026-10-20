import logging
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import Settings
from app.discovery.provider import (
    DiscoveryProvider,
    DiscoveredVideo,
    SearchQuery,
    YouTubeProviderError,
)
from app.discovery.ranking import calculate_score
from app.discovery.schemas import (
    CandidatePageResponse,
    CandidateResponse,
    CategoriesResponse,
    DiscoverySearchRequest,
    DiscoverySearchResponse,
    EvaluateRequest,
    EvaluationResponse,
    TopCandidatesResponse,
)
from app.models import SourceVideo

router = APIRouter(prefix="/api/v1", tags=["discovery and trends"])
logger = logging.getLogger(__name__)


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def candidate_payload(candidate: SourceVideo) -> CandidateResponse:
    return CandidateResponse(
        video_id=candidate.platform_video_id,
        title=candidate.title,
        channel_name=candidate.channel_name,
        published_at=candidate.published_at,
        duration_seconds=candidate.duration_seconds,
        view_count=candidate.view_count,
        like_count=candidate.like_count,
        video_url=candidate.source_url,
        category=candidate.category,
        source_query=candidate.source_query,
        country_code=candidate.country_code,
        language_code=candidate.language_code,
        discovered_at=candidate.discovered_at,
        metrics_updated_at=candidate.metrics_updated_at,
        status=candidate.status,
        ranking_score=candidate.ranking_score,
        ranking_factors=candidate.ranking_factors,
        evaluated_at=candidate.evaluated_at,
    )


def _search_jobs(body: DiscoverySearchRequest) -> list[tuple[str, str | None]]:
    if body.categories and body.search_terms:
        return [
            (f"{term} {category}", category)
            for category in body.categories
            for term in body.search_terms
        ]
    if body.categories:
        return [(category, category) for category in body.categories]
    return [(term, None) for term in body.search_terms]


def _store_discovered(
    session: Session,
    item: DiscoveredVideo,
    query: SearchQuery,
    now: datetime,
) -> tuple[SourceVideo, bool]:
    candidate = (
        session.query(SourceVideo)
        .filter(SourceVideo.platform_video_id == item.video_id)
        .one_or_none()
    )
    is_new = candidate is None
    if is_new:
        candidate = SourceVideo(
            platform_video_id=item.video_id,
            title=item.title,
            source_url=f"https://www.youtube.com/watch?v={item.video_id}",
            status="discovered",
            discovered_at=now,
            metric_history=[],
        )
        session.add(candidate)

    candidate.title = item.title
    candidate.source_url = f"https://www.youtube.com/watch?v={item.video_id}"
    candidate.channel_name = item.channel_name or candidate.channel_name
    candidate.published_at = item.published_at or candidate.published_at
    candidate.duration_seconds = (
        item.duration_seconds if item.duration_seconds is not None else candidate.duration_seconds
    )
    candidate.category = query.category or candidate.category
    candidate.source_query = query.query
    candidate.country_code = query.country_code or candidate.country_code
    candidate.language_code = query.language_code or candidate.language_code

    received_metrics = item.view_count is not None or item.like_count is not None
    if item.view_count is not None:
        candidate.view_count = item.view_count
        history = list(candidate.metric_history or [])
        history.append({"sampled_at": now.isoformat(), "view_count": item.view_count})
        candidate.metric_history = history[-20:]
    if item.like_count is not None:
        candidate.like_count = item.like_count
    if received_metrics:
        candidate.metrics_updated_at = now
    return candidate, is_new


@router.post(
    "/discovery/search",
    response_model=DiscoverySearchResponse,
    summary="Search YouTube for candidate videos",
)
def search_candidates(
    body: DiscoverySearchRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> DiscoverySearchResponse:
    settings: Settings = request.app.state.settings
    unknown_categories = set(body.categories) - set(settings.supported_categories)
    if unknown_categories:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported categories: {', '.join(sorted(unknown_categories))}",
        )

    jobs = _search_jobs(body)
    if len(jobs) > settings.youtube_max_queries_per_request:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This request expands to {len(jobs)} YouTube searches; the configured limit is "
                f"{settings.youtube_max_queries_per_request}. Reduce terms/categories or raise "
                "YOUTUBE_MAX_QUERIES_PER_REQUEST deliberately."
            ),
        )

    provider: DiscoveryProvider = request.app.state.discovery_provider
    discovered: dict[str, tuple[DiscoveredVideo, SearchQuery]] = {}
    results_received = 0
    try:
        for query_text, category in jobs:
            query = SearchQuery(
                query=query_text,
                category=category,
                country_code=body.country_code.upper() if body.country_code else None,
                language_code=body.language_code,
                published_after=body.published_after,
                published_before=body.published_before,
                max_results=body.max_results,
            )
            results = provider.search(query)
            results_received += len(results)
            for item in results:
                prior = discovered.get(item.video_id)
                if prior is None or (prior[0].view_count is None and item.view_count is not None):
                    discovered[item.video_id] = (item, query)
    except YouTubeProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    created = 0
    refreshed = 0
    saved: list[SourceVideo] = []
    try:
        for item, query in discovered.values():
            candidate, is_new = _store_discovered(session, item, query, now)
            created += int(is_new)
            refreshed += int(not is_new)
            saved.append(candidate)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Could not persist discovered YouTube candidates")
        raise

    for candidate in saved:
        session.refresh(candidate)
    logger.info(
        "YouTube discovery completed",
        extra={
            "queries_run": len(jobs),
            "results_received": results_received,
            "created_count": created,
            "refreshed_count": refreshed,
        },
    )
    return DiscoverySearchResponse(
        queries_run=len(jobs),
        results_received=results_received,
        created=created,
        refreshed=refreshed,
        candidates=[candidate_payload(candidate) for candidate in saved],
    )


@router.get(
    "/videos/candidates",
    response_model=CandidatePageResponse,
    summary="List discovered candidates",
)
def list_candidates(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: str | None = None,
    status: str | None = None,
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    language_code: str | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=100),
    min_score: float | None = Query(default=None, ge=0, le=100),
) -> CandidatePageResponse:
    query = session.query(SourceVideo)
    if category:
        query = query.filter(SourceVideo.category == category.strip().lower())
    if status:
        query = query.filter(SourceVideo.status == status)
    if country_code:
        query = query.filter(SourceVideo.country_code == country_code.upper())
    if language_code:
        query = query.filter(SourceVideo.language_code == language_code)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(SourceVideo.title.ilike(pattern), SourceVideo.channel_name.ilike(pattern)))
    if min_score is not None:
        query = query.filter(SourceVideo.ranking_score >= min_score)

    total = query.with_entities(func.count(SourceVideo.id)).scalar() or 0
    items = (
        query.order_by(SourceVideo.discovered_at.desc(), SourceVideo.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CandidatePageResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[candidate_payload(candidate) for candidate in items],
    )


@router.get(
    "/videos/candidates/{video_id}",
    response_model=CandidateResponse,
    summary="Get candidate metadata and ranking factors",
)
def get_candidate(
    video_id: str = Path(min_length=1, max_length=128),
    session: Session = Depends(get_session),
) -> CandidateResponse:
    candidate = (
        session.query(SourceVideo)
        .filter(SourceVideo.platform_video_id == video_id)
        .one_or_none()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate_payload(candidate)


@router.post(
    "/trends/evaluate",
    response_model=EvaluationResponse,
    summary="Evaluate candidates and store auditable ranking factors",
)
def evaluate_candidates(
    body: EvaluateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> EvaluationResponse:
    query = session.query(SourceVideo)
    if body.video_ids:
        query = query.filter(SourceVideo.platform_video_id.in_(body.video_ids))
    else:
        query = query.order_by(SourceVideo.discovered_at.desc(), SourceVideo.id.desc())
    candidates = query.limit(body.limit).all()
    settings: Settings = request.app.state.settings
    evaluated_at = datetime.now(timezone.utc)
    for candidate in candidates:
        candidate.ranking_score, candidate.ranking_factors = calculate_score(
            candidate, settings, now=evaluated_at
        )
        candidate.evaluated_at = evaluated_at
    session.commit()
    candidates.sort(key=lambda item: item.ranking_score or 0, reverse=True)
    logger.info("Candidate ranking completed", extra={"evaluated": len(candidates)})
    return EvaluationResponse(
        evaluated=len(candidates),
        items=[candidate_payload(candidate) for candidate in candidates],
    )


@router.get(
    "/trends/top",
    response_model=TopCandidatesResponse,
    summary="Return the highest-ranked shortlist",
)
def top_candidates(
    session: Session = Depends(get_session),
    limit: int = Query(default=7, ge=1, le=50),
    category: str | None = None,
) -> TopCandidatesResponse:
    query = session.query(SourceVideo).filter(SourceVideo.ranking_score.is_not(None))
    if category:
        query = query.filter(SourceVideo.category == category.strip().lower())
    items = (
        query.order_by(SourceVideo.ranking_score.desc(), SourceVideo.evaluated_at.desc())
        .limit(limit)
        .all()
    )
    return TopCandidatesResponse(
        count=len(items),
        items=[candidate_payload(candidate) for candidate in items],
    )


@router.get("/categories", response_model=CategoriesResponse, summary="List configured categories")
def list_categories(request: Request) -> CategoriesResponse:
    settings: Settings = request.app.state.settings
    return CategoriesResponse(categories=settings.supported_categories)