import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import Settings


class MediaToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    width: int
    height: int
    has_video: bool
    has_audio: bool


def _resolve_binary(binary: str) -> str | None:
    if os.path.sep in binary:
        path = Path(binary)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(binary)


def _filter_escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_ffmpeg_command(
    source_path: Path,
    output_path: Path,
    caption_path: Path,
    settings: Settings,
    duration_seconds: int,
    narration_path: Path | None = None,
) -> list[str]:
    style = (
        f"FontName={settings.caption_font_name},FontSize={settings.caption_font_size},"
        f"PrimaryColour={settings.caption_primary_color},"
        f"OutlineColour={settings.caption_outline_color},BorderStyle=1,Outline={settings.caption_outline_size},"
        f"Shadow=0,MarginV={settings.caption_margin_vertical},Alignment=2"
    )
    subtitles = f"subtitles='{_filter_escape(caption_path)}':force_style='{style}'"
    video_filter = (
        f"scale={settings.video_width}:{settings.video_height}:force_original_aspect_ratio=decrease,"
        f"pad={settings.video_width}:{settings.video_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,{subtitles}"
    )
    command = [settings.ffmpeg_binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_path)]
    if narration_path:
        command.extend(["-i", str(narration_path)])
    command.extend(
        [
            "-t", str(duration_seconds),
            "-vf", video_filter,
            "-r", str(settings.video_fps),
            "-c:v", "libx264",
            "-preset", settings.video_preset,
            "-crf", str(settings.video_crf),
            "-pix_fmt", "yuv420p",
        ]
    )
    if narration_path:
        command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", settings.audio_bitrate, "-shortest"])
    else:
        command.extend(["-map", "0:v:0", "-map", "0:a?", "-c:a", "aac", "-b:a", settings.audio_bitrate])
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


class FFmpegMediaProcessor:
    def __init__(self, settings: Settings, runner: Callable = subprocess.run):
        self.settings = settings
        self.runner = runner

    def probe(self, path: Path) -> MediaInfo:
        binary = _resolve_binary(self.settings.ffprobe_binary)
        if not binary:
            raise MediaToolError("ffprobe is not installed or configured")
        command = [
            binary,
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(path),
        ]
        result = self.runner(command, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise MediaToolError("Media file could not be probed; verify it is a supported video")
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams", [])
            video = next(stream for stream in streams if stream.get("codec_type") == "video")
            duration = float(payload.get("format", {}).get("duration", 0))
        except (ValueError, TypeError, StopIteration, AttributeError) as exc:
            raise MediaToolError("Media probe returned incomplete video metadata") from exc
        return MediaInfo(
            duration_seconds=duration,
            width=int(video.get("width", 0)),
            height=int(video.get("height", 0)),
            has_video=True,
            has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        )

    def render(
        self,
        source_path: Path,
        output_path: Path,
        caption_path: Path,
        duration_seconds: int,
        narration_path: Path | None = None,
    ) -> None:
        if not _resolve_binary(self.settings.ffmpeg_binary):
            raise MediaToolError("ffmpeg is not installed or configured")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = build_ffmpeg_command(
            source_path,
            output_path,
            caption_path,
            self.settings,
            duration_seconds,
            narration_path,
        )
        result = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=max(120, duration_seconds * 10),
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or "unknown error")[-1000:]
            for path in (source_path, output_path, caption_path, narration_path):
                if path:
                    details = details.replace(str(path), "<local-media>")
            raise MediaToolError("FFmpeg render failed: " + details)