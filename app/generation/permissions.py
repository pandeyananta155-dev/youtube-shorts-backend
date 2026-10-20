import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
BLOCKED_MEDIA_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com",
    "googlevideo.com", "googleusercontent.com",
}


class SourceValidationError(ValueError):
    pass


def validate_authorized_video_url(url: str, resolver=socket.getaddrinfo) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise SourceValidationError("Source URL must be a public HTTPS URL without embedded credentials")
    if parsed.port not in (None, 443):
        raise SourceValidationError("Source URL must use HTTPS on the standard port")
    if any(hostname == blocked or hostname.endswith("." + blocked) for blocked in BLOCKED_MEDIA_HOSTS):
        raise SourceValidationError("YouTube URLs cannot be downloaded; provide a separately authorized media file")
    try:
        address = ipaddress.ip_address(hostname)
        addresses = [address]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in resolver(hostname, 443, type=socket.SOCK_STREAM)
            ]
        except (OSError, ValueError) as exc:
            raise SourceValidationError("Source URL hostname could not be resolved safely") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceValidationError("Source URL must resolve only to public IP addresses")
    return url


class AuthorizedUrlFetcher:
    def __init__(self, max_bytes: int, timeout_seconds: float = 20, transport=None, resolver=socket.getaddrinfo):
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.resolver = resolver

    def download(self, url: str, destination_directory: Path) -> tuple[Path, str]:
        validate_authorized_video_url(url, resolver=self.resolver)
        extension = Path(urlsplit(url).path).suffix.lower()
        if extension not in SUPPORTED_VIDEO_EXTENSIONS:
            raise SourceValidationError("URL must end in a supported video file extension")
        destination_directory.mkdir(parents=True, exist_ok=True)
        destination = destination_directory / f"source_{uuid4().hex}{extension}"
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                with client.stream("GET", url) as response:
                    if response.status_code >= 300:
                        raise SourceValidationError("Authorized source URL did not return a direct video response")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                        raise SourceValidationError("Source URL did not return a supported video content type")
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except ValueError as exc:
                            raise SourceValidationError("Source URL returned an invalid content length") from exc
                        if declared_size > self.max_bytes:
                            raise SourceValidationError("Source video exceeds the configured upload limit")
                    total = 0
                    with destination.open("xb") as output:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise SourceValidationError("Source video exceeds the configured upload limit")
                            output.write(chunk)
                    if total == 0:
                        raise SourceValidationError("Source URL returned an empty file")
            return destination, content_type
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def ensure_within_directory(path: Path, directory: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = directory.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise SourceValidationError("Media path is outside the configured storage directory")
    return resolved_path