from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiscoverySearchRequest(BaseModel):
    search_terms: list[str] = Field(default_factory=list, max_length=20)
    categories: list[str] = Field(default_factory=list, max_length=10)
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    language_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$")
    published_after: datetime | None = None
    published_before: datetime | None = None
    max_results: int = Field(default=10, ge=1, le=50)

    @field_validator("search_terms")
    @classmethod
    def normalize_terms(cls, terms: list[str]) -> list[str]:
        normalized = [term.strip() for term in terms if term.strip()]
        if any(len(term) > 100 for term in normalized):
            raise ValueError("Search terms must be 100 characters or fewer")
        return normalized

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, categories: list[str]) -> list[str]:
        return list(dict.fromkeys(category.strip().lower() for category in categories if category.strip()))

    @model_validator(mode="after")
    def validate_search_range(self) -> "DiscoverySearchRequest":
        if self.published_after and self.published_after.tzinfo is None:
            raise ValueError("published_after must include a timezone")
        if self.published_before and self.published_before.tzinfo is None:
            raise ValueError("published_before must include a timezone")
        if not self.search_terms and not self.categories:
            raise ValueError("Provide at least one search term or category")
        if self.published_after and self.published_before:
            if self.published_after >= self.published_before:
                raise ValueError("published_after must be earlier than published_before")
        return self


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    channel_name: str | None
    published_at: datetime | None
    duration_seconds: int | None
    view_count: int | None
    like_count: int | None
    video_url: str
    category: str | None
    source_query: str | None
    country_code: str | None
    language_code: str | None
    discovered_at: datetime | None
    metrics_updated_at: datetime | None
    status: str
    ranking_score: float | None
    ranking_factors: dict[str, Any] | None
    evaluated_at: datetime | None


class DiscoverySearchResponse(BaseModel):
    queries_run: int
    results_received: int
    created: int
    refreshed: int
    candidates: list[CandidateResponse]


class CandidatePageResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CandidateResponse]


class EvaluateRequest(BaseModel):
    video_ids: list[str] | None = Field(default=None, max_length=500)
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("video_ids")
    @classmethod
    def normalize_video_ids(cls, ids: list[str] | None) -> list[str] | None:
        if ids is None:
            return ids
        normalized = list(dict.fromkeys(video_id.strip() for video_id in ids if video_id.strip()))
        if not normalized:
            raise ValueError("video_ids must contain at least one non-empty ID")
        return normalized


class EvaluationResponse(BaseModel):
    evaluated: int
    items: list[CandidateResponse]


class TopCandidatesResponse(BaseModel):
    count: int
    items: list[CandidateResponse]


class CategoriesResponse(BaseModel):
    categories: list[str]