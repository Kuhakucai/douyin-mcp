"""Restricted media download, probing, and deterministic audio-source selection."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from ..errors import MEDIA_NO_AUDIO_TRACK, MEDIA_REJECTED, AppError
from .models import EphemeralRequest, MediaAsset, MediaCandidate

ALLOWED_MEDIA_HOST_SUFFIXES = (
    ".douyinvod.com",
    ".douyinpic.com",
    ".byteimg.com",
    ".ibytedtos.com",
)
ALLOWED_REQUEST_HEADERS = {"accept", "accept-language", "range", "referer", "user-agent"}


@dataclass(frozen=True, slots=True)
class ProbeResult:
    media_role: str
    duration_ms: int | None
    container: str | None
    audio_codec: str | None
    video_codec: str | None
    sample_rate: int | None
    channels: int | None
    audio_bitrate: int | None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        return None


class SecureMediaDownloader:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_bytes: int,
        min_free_bytes: int,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        opener: Any | None = None,
        chunk_size: int = 1024 * 1024,
    ):
        self.data_dir = data_dir.resolve()
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes
        self.resolver = resolver
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        self.chunk_size = chunk_size

    def download(
        self,
        request: EphemeralRequest,
        destination: Path,
        *,
        probe: Callable[[Path], ProbeResult],
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[str, int, ProbeResult]:
        destination = destination.resolve()
        try:
            destination.relative_to(self.data_dir)
        except ValueError as exc:
            raise AppError(MEDIA_REJECTED, "Media destination must stay under DATA_DIR.") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.part")
        url, headers = request.reveal_for_internal_use()
        digest = hashlib.sha256()
        total = 0
        try:
            response = self._open(url, headers)
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if not (
                content_type.startswith("audio/")
                or content_type.startswith("video/")
                or "octet-stream" in content_type
            ):
                raise AppError(MEDIA_REJECTED, "Response content type is not media.")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > self.max_bytes:
                raise AppError(MEDIA_REJECTED, "Media exceeds the configured byte limit.")
            with part.open("xb") as handle:
                while True:
                    if cancelled and cancelled():
                        raise AppError(MEDIA_REJECTED, "Media download was cancelled.", retryable=True)
                    if shutil.disk_usage(self.data_dir).free < self.min_free_bytes:
                        raise AppError(MEDIA_REJECTED, "Insufficient free disk space.")
                    chunk = response.read(self.chunk_size)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise AppError(MEDIA_REJECTED, "Media exceeds the configured byte limit.")
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if total == 0:
                raise AppError(MEDIA_REJECTED, "Media response was empty.")
            probe_result = probe(part)
            part.replace(destination)
            return digest.hexdigest(), total, probe_result
        finally:
            part.unlink(missing_ok=True)

    def _open(self, url: str, headers: dict[str, str]) -> Any:
        current = url
        safe_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() in ALLOWED_REQUEST_HEADERS
        }
        safe_headers["Accept-Encoding"] = "identity"
        for _ in range(4):
            self._validate_url(current)
            req = urllib.request.Request(current, headers=safe_headers, method="GET")
            try:
                response = self.opener.open(req, timeout=30)
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise AppError(MEDIA_REJECTED, "Redirect has no Location header.") from exc
                current = urljoin(current, location)
                continue
            status = int(getattr(response, "status", 200))
            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise AppError(MEDIA_REJECTED, "Redirect has no Location header.")
                current = urljoin(current, location)
                continue
            return response
        raise AppError(MEDIA_REJECTED, "Too many media redirects.")

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise AppError(MEDIA_REJECTED, "Only approved HTTPS media URLs are allowed.")
        if parsed.port not in {None, 443}:
            raise AppError(MEDIA_REJECTED, "Non-standard media ports are rejected.")
        host = parsed.hostname.lower()
        if not any(host == suffix[1:] or host.endswith(suffix) for suffix in ALLOWED_MEDIA_HOST_SUFFIXES):
            raise AppError(MEDIA_REJECTED, "Media host is not approved.")
        try:
            addresses = {
                item[4][0]
                for item in self.resolver(host, 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise AppError(MEDIA_REJECTED, "Media host DNS resolution failed.", retryable=True) from exc
        if not addresses:
            raise AppError(MEDIA_REJECTED, "Media host resolved to no address.")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise AppError(MEDIA_REJECTED, "Private or reserved media addresses are rejected.")


class FFprobe:
    def __init__(self, runner: Any, executable: str = "ffprobe", timeout: int = 60):
        self.runner = runner
        self.executable = executable
        self.timeout = timeout

    def probe(self, path: Path) -> ProbeResult:
        result = self.runner.run(
            [
                self.executable,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            timeout=self.timeout,
        )
        try:
            payload = json.loads(result.stdout)
        except (ValueError, AttributeError) as exc:
            raise AppError(MEDIA_REJECTED, "FFprobe returned invalid JSON.") from exc
        streams = payload.get("streams") if isinstance(payload, dict) else []
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        if audio and video:
            role = "audiovisual"
        elif audio:
            role = "audio_only"
        elif video:
            role = "video_only"
        else:
            raise AppError(MEDIA_REJECTED, "Media contains no decodable audio or video stream.")
        format_data = payload.get("format") or {}
        duration = format_data.get("duration") or (audio or video or {}).get("duration")
        return ProbeResult(
            role,
            int(float(duration) * 1000) if duration not in (None, "N/A") else None,
            str(format_data.get("format_name") or "") or None,
            str(audio.get("codec_name") or "") or None if audio else None,
            str(video.get("codec_name") or "") or None if video else None,
            int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
            int(audio["channels"]) if audio and audio.get("channels") else None,
            int(audio["bit_rate"]) if audio and audio.get("bit_rate") else None,
        )


def select_transcription_asset(assets: list[MediaAsset]) -> MediaAsset:
    with_audio = [
        asset for asset in assets if asset.media_role in {"audio_only", "audiovisual"}
    ]
    if not with_audio:
        raise AppError(MEDIA_NO_AUDIO_TRACK, "Observed media has no decodable audio track.")
    return min(
        with_audio,
        key=lambda asset: (
            0 if asset.media_role == "audio_only" else 1,
            -(asset.sample_rate or 0),
            asset.size_bytes,
            asset.asset_id,
        ),
    )
