from pathlib import Path
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import create_database_engine
from app.generation.media import FFmpegMediaProcessor, MediaInfo, MediaToolError, build_ffmpeg_command
from app.generation.permissions import (
    AuthorizedUrlFetcher,
    SourceValidationError,
    ensure_within_directory,
    validate_authorized_video_url,
)
from app.generation.providers import TemplateCaptionProvider, TemplateScriptProvider
from app.generation.service import GenerationService
from app.main import create_app
from app.models import GeneratedShort, SourceVideo


class FakeMediaProcessor:
    def __init__(self):
        self.render_calls = 0
        self.fail_render_count = 0
        self.fail_probe = False
        self.invalid_output = False

    def probe(self, path):
        if self.fail_probe:
            raise MediaToolError("unsupported fixture media")
        if ".partial.mp4" in path.name:
            if self.invalid_output:
                return MediaInfo(40, 720, 1280, True, True)
            return MediaInfo(40, 1080, 1920, True, True)
        return MediaInfo(60, 1280, 720, True, True)

    def render(self, source_path, output_path, caption_path, duration_seconds, narration_path=None):
        self.render_calls += 1
        if self.fail_render_count:
            self.fail_render_count -= 1
            raise MediaToolError("mock FFmpeg render failure")
        output_path.write_bytes(b"mock-mp4-data")


class FixedScriptProvider:
    def generate(self, title, commentary, category):
        return "Original commentary for this authorized source."


class FixedCaptionProvider:
    def generate(self, script, duration_seconds):
        return "1\n00:00:00,000 --> 00:00:02,000\nOriginal commentary\n"


@pytest.fixture
def generation_app(tmp_path):
    engines = []

    def create(**overrides):
        index = len(engines)
        database_url = f"sqlite:///{tmp_path / f'phase3-{index}.db'}"
        engine = create_database_engine(database_url)
        engines.append(engine)
        settings = Settings(
            database_url=database_url,
            source_media_directory=tmp_path / f"media-{index}",
            generation_output_directory=tmp_path / f"outputs-{index}",
            **overrides,
        )
        media_processor = FakeMediaProcessor()
        app = create_app(settings=settings, engine=engine, media_processor=media_processor)
        app.state.generation_service = GenerationService(
            settings,
            media_processor,
            script_provider=FixedScriptProvider(),
            caption_provider=FixedCaptionProvider(),
        )
        return TestClient(app), app, media_processor

    yield create
    for engine in engines:
        engine.dispose()


def upload_source(
    client,
    *,
    filename="owned.mp4",
    data=b"authorized fixture",
    rights_verified="true",
    candidate_video_id=None,
):
    return client.post(
        "/api/v1/shorts/sources/upload",
        files={"file": (filename, data, "video/mp4")},
        data={
            "creator": "Test creator",
            "license_or_permission": "Owned by test creator",
            "rights_verified": rights_verified,
            "candidate_video_id": candidate_video_id or "",
        },
    )


def test_upload_requires_permission_and_rejects_unsupported_or_oversized_files(generation_app):
    client, app, _ = generation_app(max_source_file_mb=1)
    with client:
        no_permission = upload_source(client, rights_verified="false")
        unsupported = upload_source(client, filename="source.exe")
        oversized = upload_source(client, data=b"x" * (1024 * 1024 + 1))

    assert no_permission.status_code == 403
    assert unsupported.status_code == 415
    assert oversized.status_code == 413
    assert list(app.state.settings.source_media_directory.glob("*")) == []


def test_upload_rejects_unreadable_media_and_removes_staging_file(generation_app):
    client, app, media = generation_app()
    media.fail_probe = True
    with client:
        response = upload_source(client)

    assert response.status_code == 503
    assert "Cannot validate uploaded video" in response.json()["error"]["message"]
    assert list(app.state.settings.source_media_directory.glob("*")) == []


def test_user_upload_and_generation_save_reviewable_draft_and_output(generation_app):
    client, app, media = generation_app()
    with client:
        source_response = upload_source(client)
        assert source_response.status_code == 201, source_response.text
        source = source_response.json()
        job_response = client.post(
            "/api/v1/shorts/generate",
            json={"source_id": source["source_id"], "commentary": "My original explanation", "duration_seconds": 30},
        )

        job = job_response.json()
        listed = client.get("/api/v1/shorts/jobs?status=completed")
        preview = client.get(job["preview_url"])

    assert job_response.status_code == 201, job_response.text
    assert job["status"] == "COMPLETED"
    assert job["width"] == 1080 and job["height"] == 1920
    assert job["script"] == "Original commentary for this authorized source."
    assert job["captions"].startswith("1\n")
    assert "#Shorts" in job["draft_description"]
    assert job["output_filename"] == f"short_{job['job_id']}.mp4"
    assert listed.json()["total"] == 1
    assert preview.status_code == 200
    assert preview.content == b"mock-mp4-data"
    assert media.render_calls == 1
    assert Path(app.state.settings.generation_output_directory / job["output_filename"]).is_file()


def test_custom_caption_override_tags_are_removed(generation_app):
    client, _, _ = generation_app()
    with client:
        source = upload_source(client).json()
        response = client.post(
            "/api/v1/shorts/generate",
            json={
                "source_id": source["source_id"],
                "captions": "1\n00:00:00,000 --> 00:00:02,000\n{\\an8\\pos(1,1)}Safe caption <b>bold</b>\n",
            },
        )

    assert response.status_code == 201
    assert "{" not in response.json()["captions"]
    assert "<b>" not in response.json()["captions"]
    assert "Safe caption bold" in response.json()["captions"]


def test_discovered_youtube_candidate_is_blocked_until_authorized_media_is_attached(generation_app):
    client, app, media = generation_app()
    with client:
        with app.state.session_factory() as session:
            candidate = SourceVideo(
                platform_video_id="candidate001",
                title="Candidate source",
                source_url="https://www.youtube.com/watch?v=candidate001",
                status="discovered",
            )
            session.add(candidate)
            session.commit()

        blocked = client.post(
            "/api/v1/shorts/generate",
            json={"candidate_video_id": "candidate001"},
        )
        blocked_job = blocked.json()
        assert blocked_job["status"] == "REQUIRES_PERMISSION"
        assert media.render_calls == 0

        attached = upload_source(client, candidate_video_id="candidate001")
        assert attached.status_code == 201, attached.text
        retried = client.post(f"/api/v1/shorts/jobs/{blocked_job['job_id']}/retry")

    assert retried.json()["status"] == "COMPLETED"
    assert media.render_calls == 1


def test_duplicate_job_reuses_id_and_failed_job_retries(generation_app):
    client, _, media = generation_app()
    media.fail_render_count = 1
    with client:
        source = upload_source(client).json()
        body = {"source_id": source["source_id"], "script": "A user-approved original script."}
        first = client.post("/api/v1/shorts/generate", json=body).json()
        duplicate = client.post("/api/v1/shorts/generate", json=body).json()
        retried = client.post(f"/api/v1/shorts/jobs/{first['job_id']}/retry").json()
        completed_duplicate = client.post("/api/v1/shorts/generate", json=body).json()

    assert first["status"] == "FAILED"
    assert "mock FFmpeg render failure" in first["error"]
    assert duplicate["job_id"] == first["job_id"]
    assert duplicate["attempt_count"] == 1
    assert retried["status"] == "COMPLETED"
    assert retried["attempt_count"] == 2
    assert completed_duplicate["job_id"] == first["job_id"]
    assert completed_duplicate["attempt_count"] == 2
    assert media.render_calls == 2


def test_generation_attempt_limit_blocks_excessive_retry(generation_app):
    client, _, media = generation_app(max_generation_attempts=1)
    media.fail_render_count = 1
    with client:
        source = upload_source(client).json()
        failed = client.post(
            "/api/v1/shorts/generate",
            json={"source_id": source["source_id"]},
        ).json()
        retry = client.post(f"/api/v1/shorts/jobs/{failed['job_id']}/retry")

    assert failed["status"] == "FAILED"
    assert retry.status_code == 409
    assert "Maximum generation attempts" in retry.json()["error"]["message"]


def test_missing_narration_configuration_fails_job_with_actionable_error(generation_app):
    client, _, media = generation_app(narration_provider="none")
    with client:
        source = upload_source(client).json()
        result = client.post(
            "/api/v1/shorts/generate",
            json={"source_id": source["source_id"], "include_narration": True},
        ).json()

    assert result["status"] == "FAILED"
    assert "NARRATION_PROVIDER" in result["error"]
    assert result["script"] == "Original commentary for this authorized source."
    assert result["captions"].startswith("1\n")
    assert media.render_calls == 0


def test_output_validation_failure_is_recorded(generation_app):
    client, _, media = generation_app()
    media.invalid_output = True
    with client:
        source = upload_source(client).json()
        result = client.post(
            "/api/v1/shorts/generate",
            json={"source_id": source["source_id"]},
        ).json()

    assert result["status"] == "FAILED"
    assert "resolution/duration validation" in result["error"]


def test_preview_rejects_output_outside_configured_directory(generation_app, tmp_path):
    client, app, _ = generation_app()
    with client:
        with app.state.session_factory() as session:
            source = SourceVideo(
                platform_video_id="preview_source",
                title="Preview source",
                source_url="local-media://preview_source",
            )
            session.add(source)
            session.flush()
            job = GeneratedShort(
                source_video_id=source.id,
                config_hash="preview-test-hash",
                status="COMPLETED",
                output_path=str(tmp_path / "outside.mp4"),
            )
            session.add(job)
            session.commit()
            job_id = job.id

        response = client.get(f"/api/v1/shorts/jobs/{job_id}/preview")

    assert response.status_code == 404


def test_authorized_url_source_uses_direct_user_authorized_asset(monkeypatch, generation_app):
    def fake_download(self, url, destination_directory):
        destination_directory.mkdir(parents=True, exist_ok=True)
        path = destination_directory / "source_authorized.mp4"
        path.write_bytes(b"authorized-url-video")
        return path, "video/mp4"

    monkeypatch.setattr(AuthorizedUrlFetcher, "download", fake_download)
    client, _, _ = generation_app()
    with client:
        response = client.post(
            "/api/v1/shorts/sources/url",
            json={
                "source_url": "https://media.example.org/owned.mp4",
                "creator": "Test creator",
                "license_or_permission": "Licensed for reuse",
                "rights_verified": True,
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["source_type"] == "authorized_url"
    assert response.json()["rights_verification_status"] == "VERIFIED"
    assert "media.example.org" not in response.text


def test_child_category_rejects_personal_contact_information(generation_app):
    client, app, media = generation_app()
    with client:
        source = upload_source(client).json()
        with app.state.session_factory() as session:
            source_row = session.get(SourceVideo, source["source_id"])
            source_row.category = "children"
            session.commit()
        result = client.post(
            "/api/v1/shorts/generate",
            json={"source_id": source["source_id"], "script": "Call us at 212-555-0199."},
        ).json()

    assert result["status"] == "FAILED"
    assert "personal contact information" in result["error"]
    assert media.render_calls == 0


def test_authorized_url_blocks_youtube_and_private_hosts():
    with pytest.raises(SourceValidationError, match="YouTube URLs"):
        validate_authorized_video_url("https://youtube.com/watch/video.mp4")
    with pytest.raises(SourceValidationError, match="public IP"):
        validate_authorized_video_url("https://127.0.0.1/source.mp4")


def test_authorized_url_fetcher_uses_public_direct_video_and_caps_bytes(tmp_path):
    def resolver(host, port, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"video-data")
    )
    fetcher = AuthorizedUrlFetcher(
        max_bytes=64,
        transport=transport,
        resolver=resolver,
    )

    path, content_type = fetcher.download("https://media.example.org/clip.mp4", tmp_path)

    assert content_type == "video/mp4"
    assert path.read_bytes() == b"video-data"
    assert path.parent == tmp_path


def test_local_paths_reject_traversal_and_settings_hide_absolute_output_path(generation_app, tmp_path):
    with pytest.raises(SourceValidationError, match="outside"):
        ensure_within_directory(tmp_path / "outside.mp4", tmp_path / "media")
    client, app, _ = generation_app()
    with client:
        settings = client.get("/api/v1/shorts/settings").json()

    assert settings["output_directory"] == Path(app.state.settings.generation_output_directory).name
    assert str(tmp_path) not in str(settings)


def test_ffmpeg_command_builds_vertical_captioned_mp4_without_shell_execution(tmp_path):
    settings = Settings(ffmpeg_binary="/bin/true")
    command = build_ffmpeg_command(
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        tmp_path / "captions.srt",
        settings,
        duration_seconds=30,
    )

    assert command[0] == "/bin/true"
    assert "1080:1920" in command[command.index("-vf") + 1]
    assert "subtitles=" in command[command.index("-vf") + 1]
    assert command[-1] == str(tmp_path / "output.mp4")


def test_render_fails_clearly_when_ffmpeg_is_not_installed(tmp_path):
    processor = FFmpegMediaProcessor(Settings(ffmpeg_binary="phase3-ffmpeg-not-installed"))

    with pytest.raises(MediaToolError, match="ffmpeg is not installed"):
        processor.render(
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            tmp_path / "captions.srt",
            duration_seconds=30,
        )


def test_generation_settings_show_tool_availability_without_secrets(generation_app):
    client, _, _ = generation_app()
    with client:
        response = client.get("/api/v1/shorts/settings")

    assert response.status_code == 200
    assert response.json()["aspect_ratio"] == "9:16"
    assert "api_key" not in response.text.lower()
