import math
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.models import SourceVideo


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _bounded_log_score(value: float, reference: float) -> float:
    if value <= 0:
        return 0.0
    return min(100.0, 100.0 * math.log1p(value) / math.log1p(reference))


def calculate_score(
    candidate: SourceVideo,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[float, dict[str, Any]]:
    evaluated_at = _as_utc(now or datetime.now(timezone.utc))
    factors: dict[str, Any] = {}
    weights: dict[str, float] = {}

    if candidate.published_at:
        age_days = max((evaluated_at - _as_utc(candidate.published_at)).total_seconds() / 86400, 0.0)
        recency = 100.0 * math.exp(
            -math.log(2) * age_days / settings.ranking_recency_half_life_days
        )
        factors["recency"] = {
            "score": round(recency, 4),
            "age_days": round(age_days, 4),
            "half_life_days": settings.ranking_recency_half_life_days,
        }
        weights["recency"] = settings.ranking_recency_weight

    if candidate.view_count is not None and candidate.published_at:
        age_days = max((evaluated_at - _as_utc(candidate.published_at)).total_seconds() / 86400, 1 / 24)
        views_per_day = candidate.view_count / age_days
        factors["views_per_day"] = {
            "score": round(_bounded_log_score(views_per_day, settings.ranking_views_per_day_reference), 4),
            "views_per_day": round(views_per_day, 2),
            "reference_views_per_day": settings.ranking_views_per_day_reference,
        }
        weights["views_per_day"] = settings.ranking_views_weight

    if candidate.view_count and candidate.like_count is not None:
        engagement_rate = candidate.like_count / candidate.view_count
        engagement_score = min(100.0, 100.0 * engagement_rate / settings.ranking_engagement_reference)
        factors["engagement"] = {
            "score": round(engagement_score, 4),
            "like_view_ratio": round(engagement_rate, 6),
            "reference_ratio": settings.ranking_engagement_reference,
        }
        weights["engagement"] = settings.ranking_engagement_weight

    history = candidate.metric_history or []
    metric_points = [
        point for point in history if point.get("view_count") is not None and point.get("sampled_at")
    ]
    if len(metric_points) >= 2:
        first = min(metric_points, key=lambda point: point["sampled_at"])
        last = max(metric_points, key=lambda point: point["sampled_at"])
        first_at = _as_utc(datetime.fromisoformat(first["sampled_at"]))
        last_at = _as_utc(datetime.fromisoformat(last["sampled_at"]))
        elapsed_hours = (last_at - first_at).total_seconds() / 3600
        delta_views = last["view_count"] - first["view_count"]
        if elapsed_hours >= settings.ranking_min_velocity_interval_hours and delta_views >= 0:
            views_per_day = delta_views / (elapsed_hours / 24)
            factors["view_velocity"] = {
                "score": round(_bounded_log_score(views_per_day, settings.ranking_views_per_day_reference), 4),
                "views_per_day": round(views_per_day, 2),
                "observation_hours": round(elapsed_hours, 2),
                "observations": len(metric_points),
            }
            weights["view_velocity"] = settings.ranking_velocity_weight

    total_weight = sum(weights.values())
    if total_weight == 0:
        return 0.0, {"algorithm": "shortlist-v1", "factors": factors, "weights_used": weights}

    weighted_score = sum(factors[name]["score"] * weights[name] for name in weights) / total_weight
    score = round(weighted_score, 4)
    return score, {"algorithm": "shortlist-v1", "factors": factors, "weights_used": weights}