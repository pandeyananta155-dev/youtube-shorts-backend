from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from app.models import PublishingJob, PublishingSettings, UploadHistory
ACTIVE_DAILY_STATUSES = ("PENDING", "SCHEDULED", "UPLOADING", "UPLOADED", "PUBLISHED")


def publication_slots(
    *,
    daily_target: int,
    timezone_name: str,
    allowed_days: list[int],
    publishing_windows: list[dict[str, str]],
    start_after: datetime,
    days_ahead: int = 30,
) -> list[datetime]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown publishing timezone") from exc
    if start_after.tzinfo is None:
        start_after = start_after.replace(tzinfo=timezone.utc)
    start_after = start_after.astimezone(timezone.utc)
    windows = []
    for window in publishing_windows:
        start_hour, start_minute = (int(part) for part in window["start"].split(":"))
        end_hour, end_minute = (int(part) for part in window["end"].split(":"))
        start_minute_of_day = start_hour * 60 + start_minute
        end_minute_of_day = end_hour * 60 + end_minute
        if end_minute_of_day <= start_minute_of_day:
            raise ValueError("Publishing window end must be later than start")
        windows.append((start_minute_of_day, end_minute_of_day))
    windows.sort()
    for previous, current in zip(windows, windows[1:]):
        if previous[1] > current[0]:
            raise ValueError("Publishing windows overlap")
    total_minutes = sum(end - start for start, end in windows)
    if total_minutes < daily_target:
        raise ValueError("Publishing windows must have at least one minute per daily publication")

    local_start_date = start_after.astimezone(zone).date()
    slots = []
    for day_offset in range(days_ahead + 1):
        current_date = local_start_date + timedelta(days=day_offset)
        if current_date.weekday() not in allowed_days:
            continue
        for index in range(daily_target):
            minute_offset = min(total_minutes - 1, int((index + 0.5) * total_minutes / daily_target))
            remaining = minute_offset
            local_minute = None
            for window_start, window_end in windows:
                window_length = window_end - window_start
                if remaining < window_length:
                    local_minute = window_start + remaining
                    break
                remaining -= window_length
            if local_minute is None:
                continue
            slot_local = datetime.combine(
                current_date,
                time(hour=local_minute // 60, minute=local_minute % 60),
                tzinfo=zone,
            )
            slot_utc = slot_local.astimezone(timezone.utc)
            if slot_utc > start_after:
                slots.append(slot_utc)
    return sorted(slots)


def count_daily_commitments(session: Session, settings: PublishingSettings, day: date) -> int:
    zone = ZoneInfo(settings.timezone)
    local_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    has_attempt = exists().where(UploadHistory.publishing_job_id == PublishingJob.id)
    scheduled_without_attempt = (
        session.query(func.count(PublishingJob.id))
        .filter(
            PublishingJob.scheduled_at >= local_start,
            PublishingJob.scheduled_at < local_end,
            PublishingJob.status.in_(ACTIVE_DAILY_STATUSES),
            ~has_attempt,
        )
        .scalar()
        or 0
    )
    upload_attempts = (
        session.query(func.count(UploadHistory.id))
        .filter(
            UploadHistory.upload_started_at >= local_start,
            UploadHistory.upload_started_at < local_end,
        )
        .scalar()
        or 0
    )
    return scheduled_without_attempt + upload_attempts


def count_upload_attempts(session: Session, timezone_name: str, day: date) -> int:
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return (
        session.query(func.count(UploadHistory.id))
        .filter(
            UploadHistory.upload_started_at >= local_start,
            UploadHistory.upload_started_at < local_end,
        )
        .scalar()
        or 0
    )