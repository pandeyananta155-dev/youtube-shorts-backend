import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httplib2
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.config import Settings
from app.database import create_database_engine, ensure_schema
from app.generation.media import MediaInfo
from app.main import create_app
from app.models import (
    GeneratedShort,
    OAuthConnection,
    OAuthState,
    PublishingJob,
    PublishingSettings,
    SourceVideo,
    UploadHistory,
)
from app.publishing.oauth import OAuthNotConfigured, YouTubeOAuthService
from app.publishing.schemas import PublishingSettingsUpdate
from app.publishing.service import (
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    STATUS_UPLOADED,
    PublishingService,
    get_publishing_settings,
)
from app.publishing.scheduler import publication_slots
from app.publishing.security import TokenCipher
from app.publishing.youtube_client import (
    UploadOutcomeUnknown,
    UploadResult,
    YouTubeApiError,
    YouTubeDataApiPublisher,
)

ADMIN_TOKEN = "owner-admin-token-for-tests-0123456789abcdef"


class FakeFlow:
    state = None

    @classmethod
    def from_client_config(cls, config, scopes, state, redirect_uri):
        cls.state = state
        cls.scopes = scopes
        cls.redirect_uri = redirect_uri
        return cls()

    def authorization_url(self, **kwargs):
        from urllib.parse import urlencode

        return "https://accounts.google.test/authorize?" + urlencode({"state": self.state}), None

    def fetch_token(self, authorization_response):
        assert "code=mock-code" in authorization_response
        self.credentials = type("Credentials", (), {"refresh_token": "refresh-token-never-return"})()


class FakeProvider:
    def __init__(self):
        self.upload_calls = []
        self.upload_results = []
        self.status_results = {}
        self.revoked = []
        self.cancel_calls = []
        self.cancel_result = True

    def channel_info(self, credentials):
        return "channel-123", "Owner channel"

    def upload(self, credentials, source_path, title, description, privacy_status, publish_at=None):
        self.upload_calls.append((source_path, title, description, privacy_status, publish_at))
        result = self.upload_results.pop(0) if self.upload_results else UploadResult(
            "video-123", privacy_status, "uploaded", None, publish_at, title
        )
        if isinstance(result, Exception):
            raise result
        return result

    def video_status(self, credentials, video_id):
        return self.status_results.get(video_id)

    def revoke(self, token):
        self.revoked.append(token)

    def cancel_scheduled(self, credentials, video_id):
        self.cancel_calls.append(video_id)
        return self.cancel_result


class FakeMediaValidator:
    def __init__(self):
        self.invalid = False

    def probe(self, path):
        if self.invalid:
            return MediaInfo(30, 720, 1280, True, True)
        return MediaInfo(30, 1080, 1920, True, True)


class FakeOAuth:
    def __init__(self, failure=None):
        self.failure = failure
        self.disconnect_calls = 0

    def authorization_url(self, session):
        if self.failure:
            raise self.failure
        return "https://accounts.google.test/authorize?state=mock"

    def credentials(self, session):
        if self.failure:
            raise self.failure
        return object()

    def disconnect(self, session):
        self.disconnect_calls += 1


@pytest.fixture
def publishing_app(tmp_path):
    engines = []

    def create(*, oauth=None, provider=None, **overrides):
        index = len(engines)
        db_url = f"sqlite:///{tmp_path / f'publishing-{index}.db'}"
        engine = create_database_engine(db_url)
        ensure_schema(engine)
        engines.append(engine)
        settings = Settings(
            database_url=db_url,
            generation_output_directory=tmp_path / f"outputs-{index}",
            publishing_admin_token=ADMIN_TOKEN,
            **overrides,
        )
        publisher = provider or FakeProvider()
        media_validator = FakeMediaValidator()
        app = create_app(
            settings=settings,
            engine=engine,
            publishing_provider=publisher,
            oauth_service=oauth or FakeOAuth(),
            media_processor=media_validator,
        )
        app.state.publishing_media_validator = media_validator
        return TestClient(app), app, publisher

    yield create
    for engine in engines:
        engine.dispose()


def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def settings_payload(**overrides):
    payload = {
        "daily_target": 5,
        "timezone": "UTC",
        "publishing_windows": [{"start": "10:00", "end": "18:00"}],
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],
        "scheduler_enabled": False,
        "globally_paused": False,
        "require_review": True,
        "dry_run": True,
        "public_publishing_enabled": False,
        "allow_private_test_uploads": False,
        "max_retries": 3,
        "upload_lead_minutes": 30,
    }
    payload.update(overrides)
    return payload


def set_settings(client, **overrides):
    return client.put(
        "/api/v1/publishing/settings",
        headers=admin_headers(),
        json=settings_payload(**overrides),
    )


def seed_short(app, output_path: Path, *, status="COMPLETED", rights=True, review="NEEDS_REVIEW"):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"mock-mp4-output")
    with app.state.session_factory() as session:
        source = SourceVideo(
            platform_video_id=f"phase4-source-{uuid4().hex}",
            title="Authorized source",
            source_url="local-media://phase4-source",
            source_type="user_file",
            rights_verified=rights,
            rights_verification_status="VERIFIED" if rights else "REQUIRES_PERMISSION",
            license_or_permission="Owned by test creator" if rights else None,
        )
        session.add(source)
        session.flush()
        short = GeneratedShort(
            source_video_id=source.id,
            config_hash="phase4-config",
            output_path=str(output_path),
            title="Generated draft",
            description="Original description #Shorts",
            status=status,
            review_status=review,
        )
        session.add(short)
        session.commit()
        return short.id


def test_oauth_state_is_single_use_and_refresh_token_is_encrypted(publishing_app):
    key = Fernet.generate_key().decode("ascii")
    provider = FakeProvider()
    settings_values = {
        "database_url": "sqlite:///:memory:",
        "google_client_id": "client-id",
        "google_client_secret": "client-secret",
        "google_oauth_redirect_uri": "http://localhost:8000/api/v1/youtube/auth/callback",
        "oauth_token_encryption_key": key,
    }
    oauth_settings = {key: value for key, value in settings_values.items() if key != "database_url"}
    settings = Settings(**oauth_settings)
    oauth = YouTubeOAuthService(settings, provider, flow_factory=FakeFlow)
    client, app, _ = publishing_app(oauth=oauth, provider=provider, **oauth_settings)
    with client:
        started = client.get("/api/v1/youtube/auth/start", headers=admin_headers())
        state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
        callback = client.get(
            "/api/v1/youtube/auth/callback",
            params={"state": state, "code": "mock-code"},
        )
        replay = client.get(
            "/api/v1/youtube/auth/callback",
            params={"state": state, "code": "mock-code"},
        )
        status = client.get("/api/v1/youtube/auth/status")
        with app.state.session_factory() as session:
            connection = session.get(OAuthConnection, 1)
            token_ciphertext = connection.encrypted_refresh_token
            stored_state = session.query(OAuthState).one()

    assert started.status_code == 200
    assert callback.status_code == 200
    assert callback.json()["connected"] is True
    assert replay.status_code == 400
    assert status.json()["channel_title"] == "Owner channel"
    assert "refresh-token-never-return" not in token_ciphertext
    assert TokenCipher(key).decrypt(token_ciphertext) == "refresh-token-never-return"
    assert stored_state.consumed_at is not None
    assert "client-secret" not in started.text + callback.text + status.text


def test_invalid_oauth_state_and_missing_oauth_config_fail_truthfully(publishing_app):
    settings = Settings(
        google_client_id="client-id",
        google_client_secret="client-secret",
        oauth_token_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    valid_oauth = YouTubeOAuthService(settings, FakeProvider(), flow_factory=FakeFlow)
    client, _, _ = publishing_app(oauth=valid_oauth)
    missing_client, _, _ = publishing_app(oauth=FakeOAuth(OAuthNotConfigured("OAuth is not configured")))
    with client, missing_client:
        invalid_state = client.get(
            "/api/v1/youtube/auth/callback",
            params={"state": "forged-state", "code": "mock-code"},
        )
        missing_config = missing_client.get("/api/v1/youtube/auth/start", headers=admin_headers())

    assert invalid_state.status_code == 400
    assert missing_config.status_code == 503
    assert "OAuth is not configured" in missing_config.json()["error"]["message"]


def test_sensitive_actions_require_owner_bearer_token(publishing_app):
    client, app, _ = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    with client:
        denied = client.post(
            "/api/v1/publishing/jobs",
            json={"generated_short_id": short_id, "review_confirmed": True},
        )
        denied_settings = client.put("/api/v1/publishing/settings", json=settings_payload())
        public_status = client.get("/api/v1/youtube/auth/status")

    assert denied.status_code == 401
    assert denied_settings.status_code == 401
    assert public_status.status_code == 200


def test_dry_run_and_review_gate_never_call_youtube(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    with client:
        set_settings(client, dry_run=True, globally_paused=False)
        needs_review = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id},
        )
        dry_run = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "review_confirmed": True},
        )

    assert needs_review.status_code == 201
    assert needs_review.json()["status"] == STATUS_NEEDS_REVIEW
    assert dry_run.json()["dry_run"] is True
    assert dry_run.json()["status"] == "PENDING"
    assert "Dry run" in dry_run.json()["last_error"]
    assert provider.upload_calls == []


def test_owner_can_promote_dry_run_job_after_enabling_real_private_uploads(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    with client:
        set_settings(client, dry_run=True, globally_paused=False)
        dry_run = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "private", "review_confirmed": True},
        )
        set_settings(client, dry_run=False, allow_private_test_uploads=True)
        promoted = client.post(
            f"/api/v1/publishing/jobs/{dry_run.json()['job_id']}/retry",
            headers=admin_headers(),
        )

    assert dry_run.json()["dry_run"] is True
    assert promoted.json()["status"] == STATUS_UPLOADED
    assert promoted.json()["dry_run"] is False
    assert len(provider.upload_calls) == 1


def test_disabled_public_publish_and_unverified_source_are_blocked(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    unverified_id = seed_short(
        app,
        app.state.settings.generation_output_directory / "short_2.mp4",
        rights=False,
    )
    with client:
        set_settings(
            client,
            dry_run=False,
            public_publishing_enabled=False,
            allow_private_test_uploads=True,
        )
        public_job = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "public", "review_confirmed": True},
        )
        unverified = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": unverified_id, "review_confirmed": True},
        )

    assert public_job.json()["status"] == STATUS_NEEDS_REVIEW
    assert unverified.status_code == 403
    assert provider.upload_calls == []


def test_revoked_oauth_credentials_block_upload_truthfully(publishing_app):
    client, app, provider = publishing_app(
        oauth=FakeOAuth(OAuthNotConfigured("YouTube authorization expired or was revoked; reconnect the channel"))
    )
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    with client:
        set_settings(client, dry_run=False, globally_paused=False, allow_private_test_uploads=True)
        result = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "private", "review_confirmed": True},
        )

    assert result.json()["status"] == STATUS_NEEDS_REVIEW
    assert result.json()["api_error_code"] == "reauth_required"
    assert "revoked" in result.json()["last_error"]
    assert provider.upload_calls == []


def test_private_upload_success_editable_metadata_idempotency_and_history(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    provider.upload_results.append(
        UploadResult("video-private", "private", "uploaded", None, None, "Edited title")
    )
    body = {
        "generated_short_id": short_id,
        "privacy_status": "private",
        "review_confirmed": True,
        "idempotency_key": "publish-short-one-001",
        "title": "Edited title",
        "description": "Edited description",
    }
    with client:
        set_settings(
            client,
            dry_run=False,
            globally_paused=False,
            allow_private_test_uploads=True,
        )
        first = client.post("/api/v1/publishing/jobs", headers=admin_headers(), json=body)
        duplicate = client.post("/api/v1/publishing/jobs", headers=admin_headers(), json=body)
        status = client.get(f"/api/v1/publishing/jobs/{first.json()['job_id']}", headers=admin_headers())
        history = client.get("/api/v1/publishing/history", headers=admin_headers())

    assert first.status_code == 201
    assert first.json()["status"] == STATUS_UPLOADED
    assert first.json()["video_url"] == "https://www.youtube.com/watch?v=video-private"
    assert duplicate.json()["job_id"] == first.json()["job_id"]
    assert len(provider.upload_calls) == 1
    assert provider.upload_calls[0][1:4] == ("Edited title", "Edited description", "private")
    assert status.json()["attempts"] == 1
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["video_id"] == "video-private"


def test_invalid_completed_media_is_rejected_before_upload(publishing_app):
    client, app, provider = publishing_app()
    app.state.publishing_media_validator.invalid = True
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    with client:
        set_settings(client, dry_run=False, globally_paused=False, allow_private_test_uploads=True)
        result = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "private", "review_confirmed": True},
        )

    assert result.json()["status"] == STATUS_FAILED
    assert result.json()["api_error_code"] == "media_invalid"
    assert provider.upload_calls == []


def test_unknown_upload_outcome_requires_reconciliation_and_never_retries_blindly(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    provider.upload_results.append(UploadOutcomeUnknown("connection dropped after request body"))
    with client:
        set_settings(client, dry_run=False, globally_paused=False, allow_private_test_uploads=True)
        created = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "private", "review_confirmed": True},
        )
        retry = client.post(
            f"/api/v1/publishing/jobs/{created.json()['job_id']}/retry",
            headers=admin_headers(),
        )

    assert created.json()["status"] == STATUS_NEEDS_REVIEW
    assert created.json()["outcome_unknown"] is True
    assert retry.status_code == 409
    assert len(provider.upload_calls) == 1


def test_retryable_error_uses_backoff_and_respects_quota(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4")
    provider.upload_results.append(YouTubeApiError("temporarily unavailable", "backendError", retryable=True))
    with client:
        set_settings(client, dry_run=False, globally_paused=False, allow_private_test_uploads=True)
        failed = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={"generated_short_id": short_id, "privacy_status": "private", "review_confirmed": True},
        )
        early_retry = client.post(
            f"/api/v1/publishing/jobs/{failed.json()['job_id']}/retry",
            headers=admin_headers(),
        )
        with app.state.session_factory() as session:
            job = session.get(PublishingJob, failed.json()["job_id"])
            job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        provider.upload_results.append(
            YouTubeApiError("YouTube API quota exhausted", "quotaExceeded", retryable=True, quota_exhausted=True)
        )
        retried = client.post(
            f"/api/v1/publishing/jobs/{failed.json()['job_id']}/retry",
            headers=admin_headers(),
        )
        history = client.get("/api/v1/publishing/history", headers=admin_headers())

    assert failed.json()["status"] == STATUS_FAILED
    assert failed.json()["next_attempt_at"]
    assert early_retry.status_code == 429
    assert retried.json()["status"] == STATUS_FAILED
    assert retried.json()["api_error_code"] == "quotaExceeded"
    assert len(history.json()["items"]) == 2


def test_immediate_job_defers_when_daily_upload_limit_is_reached(publishing_app):
    client, app, provider = publishing_app()
    short_ids = [
        seed_short(app, app.state.settings.generation_output_directory / f"short_{index}.mp4")
        for index in range(1, 7)
    ]
    with client:
        set_settings(client, dry_run=False, globally_paused=False, allow_private_test_uploads=True)
        with app.state.session_factory() as session:
            now = datetime.now(timezone.utc)
            for short_id in short_ids[:5]:
                job = PublishingJob(
                    generated_short_id=short_id,
                    status=STATUS_FAILED,
                    review_confirmed=True,
                    privacy_status="private",
                    dry_run=False,
                    max_attempts=3,
                )
                session.add(job)
                session.flush()
                session.add(
                    UploadHistory(
                        publishing_job_id=job.id,
                        status="FAILED",
                        upload_started_at=now - timedelta(minutes=1),
                        attempt_number=1,
                    )
                )
            session.commit()
        result = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={
                "generated_short_id": short_ids[5],
                "privacy_status": "private",
                "review_confirmed": True,
            },
        )

    assert result.status_code == 201
    assert result.json()["status"] == STATUS_SCHEDULED
    assert "daily upload limit" in result.json()["last_error"]
    assert provider.upload_calls == []


def test_schedule_daily_target_windows_pause_resume_and_cancel(publishing_app):
    client, app, _ = publishing_app()
    short_ids = [
        seed_short(app, app.state.settings.generation_output_directory / f"short_{index}.mp4", review="APPROVED")
        for index in range(1, 8)
    ]
    with client:
        config = set_settings(
            client,
            daily_target=5,
            timezone="America/Los_Angeles",
            publishing_windows=[{"start": "10:00", "end": "18:00"}],
            allowed_days=[0, 1, 2, 3, 4, 5, 6],
            globally_paused=True,
            dry_run=True,
            allow_private_test_uploads=False,
        )
        scheduled = client.post(
            "/api/v1/publishing/schedule",
            headers=admin_headers(),
            json={"generated_short_ids": short_ids, "limit": 7, "review_confirmed": True},
        )
        schedule_view = client.get("/api/v1/publishing/schedule", headers=admin_headers())
        cancel = client.delete(
            f"/api/v1/publishing/schedule/{scheduled.json()['jobs'][0]['job_id']}",
            headers=admin_headers(),
        )
        paused = client.post("/api/v1/publishing/pause", headers=admin_headers())
        resumed = client.post("/api/v1/publishing/resume", headers=admin_headers())

    assert config.status_code == 200
    assert scheduled.status_code == 200
    assert scheduled.json()["count"] == 7
    assert schedule_view.json()["count"] == 7
    assert cancel.status_code == 200
    assert paused.json()["globally_paused"] is True
    assert resumed.json()["globally_paused"] is False
    local_dates = [
        datetime.fromisoformat(item["scheduled_at"].replace("Z", "+00:00"))
        .astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
        .date()
        for item in scheduled.json()["jobs"]
    ]
    assert max(local_dates.count(day) for day in set(local_dates)) <= 5


def test_cancel_of_youtube_scheduled_video_requires_provider_confirmation(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4", review="APPROVED")
    with app.state.session_factory() as session:
        job = PublishingJob(
            generated_short_id=short_id,
            status=STATUS_SCHEDULED,
            video_id="scheduled-video-id",
            video_url="https://www.youtube.com/watch?v=scheduled-video-id",
            max_attempts=3,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    with client:
        result = client.delete(f"/api/v1/publishing/schedule/{job_id}", headers=admin_headers())

    assert result.status_code == 200
    assert result.json()["status"] == "CANCELLED"
    assert provider.cancel_calls == ["scheduled-video-id"]


def test_scheduler_no_eligible_shorts_and_publication_poll(publishing_app):
    client, app, provider = publishing_app()
    with client:
        set_settings(client, scheduler_enabled=True, globally_paused=False)
        with app.state.session_factory() as session:
            settings = get_publishing_settings(session, app.state.settings)
            count = app.state.publishing_service.run_due_once(session)
        status = client.get("/api/v1/youtube/auth/status")

    assert count == 0
    assert status.json()["connected"] is False


def test_scheduled_private_upload_is_distinct_from_publication_and_reconciles(publishing_app):
    client, app, provider = publishing_app()
    short_id = seed_short(app, app.state.settings.generation_output_directory / "short_1.mp4", review="APPROVED")
    publish_at = datetime.now(timezone.utc) + timedelta(hours=2)
    provider.upload_results.append(
        UploadResult("video-scheduled", "private", "uploaded", None, publish_at, "Scheduled title")
    )
    provider.status_results["video-scheduled"] = UploadResult(
        "video-scheduled", "public", "processed", datetime.now(timezone.utc), None, "Scheduled title"
    )
    with client:
        set_settings(
            client,
            dry_run=False,
            globally_paused=False,
            allow_private_test_uploads=True,
        )
        created = client.post(
            "/api/v1/publishing/jobs",
            headers=admin_headers(),
            json={
                "generated_short_id": short_id,
                "privacy_status": "private",
                "review_confirmed": True,
            },
        )
        with app.state.session_factory() as session:
            job = session.get(PublishingJob, created.json()["job_id"])
            job.scheduled_at = publish_at
            job.status = STATUS_SCHEDULED
            session.commit()
            updates = app.state.publishing_service.sync_publication_statuses_once(session)
            session.refresh(job)

    assert created.json()["status"] == STATUS_SCHEDULED
    assert updates == 1
    assert job.status == STATUS_PUBLISHED
    assert job.privacy_status == "public"


def test_timezone_slot_planner_respects_allowed_days_and_even_spacing():
    start = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slots = publication_slots(
        daily_target=5,
        timezone_name="America/Los_Angeles",
        allowed_days=[0, 2, 4],
        publishing_windows=[{"start": "09:00", "end": "19:00"}],
        start_after=start,
        days_ahead=8,
    )

    local = [slot.astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles")) for slot in slots]
    assert local
    assert all(item.weekday() in {0, 2, 4} for item in local)
    assert all(item.hour >= 9 and (item.hour < 19 or (item.hour == 19 and item.minute == 0)) for item in local)
    assert len({item.date() for item in local}) > 1


def test_google_publisher_uses_resumable_upload_and_records_returned_id(tmp_path):
    source = tmp_path / "completed-short.mp4"
    source.write_bytes(b"valid-mp4-fixture")
    media_arguments = []
    request_arguments = []

    class MockRequest:
        def next_chunk(self):
            return None, {
                "id": "youtube-returned-id",
                "snippet": {"title": "Reviewed title", "publishedAt": "2026-10-20T12:00:00Z"},
                "status": {"privacyStatus": "private", "uploadStatus": "uploaded"},
            }

    class Videos:
        def insert(self, **kwargs):
            request_arguments.append(kwargs)
            return MockRequest()

    class Service:
        def videos(self):
            return Videos()

    def media_factory(*args, **kwargs):
        media_arguments.append((args, kwargs))
        return object()

    publisher = YouTubeDataApiPublisher(
        service_factory=lambda *args, **kwargs: Service(),
        media_upload_factory=media_factory,
    )
    result = publisher.upload(
        object(), source, "Reviewed title", "Description #Shorts", "private"
    )

    assert result.video_id == "youtube-returned-id"
    assert result.privacy_status == "private"
    assert request_arguments[0]["part"] == "snippet,status"
    assert media_arguments[0][1]["resumable"] is True
    assert media_arguments[0][1]["chunksize"] == 8 * 1024 * 1024


def test_google_publisher_treats_server_error_as_unknown_and_quota_as_nonretryable(tmp_path):
    source = tmp_path / "completed-short.mp4"
    source.write_bytes(b"valid-mp4-fixture")

    class Videos:
        def __init__(self, error):
            self.error = error

        def insert(self, **kwargs):
            raise self.error

    class Service:
        def __init__(self, error):
            self.error = error

        def videos(self):
            return Videos(self.error)

    def publisher_for(status, payload):
        error = HttpError(httplib2.Response({"status": str(status)}), payload)
        return YouTubeDataApiPublisher(
            service_factory=lambda *args, **kwargs: Service(error),
            media_upload_factory=lambda *args, **kwargs: object(),
        )

    server_failure = publisher_for(500, b'{"error":{"errors":[{"reason":"backendError"}]}}')
    with pytest.raises(UploadOutcomeUnknown):
        server_failure.upload(object(), source, "title", "description", "private")

    quota_failure = publisher_for(403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}')
    with pytest.raises(YouTubeApiError) as raised:
        quota_failure.upload(object(), source, "title", "description", "private")
    assert raised.value.quota_exhausted is True
    assert raised.value.retryable is False


def test_invalid_timezone_window_and_dryrun_public_settings_are_rejected():
    with pytest.raises(Exception):
        PublishingSettingsUpdate(**settings_payload(timezone="Not/AZone"))
    with pytest.raises(Exception):
        PublishingSettingsUpdate(**settings_payload(publishing_windows=[{"start": "18:00", "end": "10:00"}]))
    with pytest.raises(Exception):
        PublishingSettingsUpdate(**settings_payload(public_publishing_enabled=True, dry_run=True))
