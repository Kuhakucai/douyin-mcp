"""Response helpers with exact-key and final string sanitization."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import AppError

SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "ephemeral_request",
    "headers",
    "http_api_key",
    "password",
    "refresh_token",
    "secret",
    "set_cookie",
    "signed_url",
    "token",
}
REDACTED = "[REDACTED]"
KNOWN_MEDIA_HOST_SUFFIXES = (
    ".douyinvod.com",
    ".douyinpic.com",
    ".byteimg.com",
    ".ibytedtos.com",
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
HEADER_RE = re.compile(
    r"(?im)^(?:cookie|set-cookie|authorization|proxy-authorization)\s*:\s*.*$"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-=]+")
WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\r\n:*?\"<>|]+\\)*[^\\\r\n:*?\"<>|]*")
POSIX_PATH_RE = re.compile(r"(?<![\w.])/(?:home|Users|tmp|var|private|opt)/[^\s\"']+")
MAX_STRING_LENGTH = 4096


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(("_token", "_secret", "_password"))


def _sanitize_url(match: re.Match[str]) -> str:
    raw = match.group(0).rstrip(".,);]")
    suffix = match.group(0)[len(raw) :]
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<redacted-url>" + suffix
    host = (parsed.hostname or "").lower()
    if any(host == item[1:] or host.endswith(item) for item in KNOWN_MEDIA_HOST_SUFFIXES):
        return "<redacted-media-url>" + suffix
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) + suffix


def sanitize_text(value: str, *, data_dir: Path | str | None = None) -> str:
    text = HEADER_RE.sub(lambda _: f"authorization: {REDACTED}", str(value))
    text = BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = URL_RE.sub(_sanitize_url, text)
    if data_dir is not None:
        raw = str(Path(data_dir).resolve())
        text = re.sub(re.escape(raw), "<data-dir>", text, flags=re.IGNORECASE)
    text = WINDOWS_PATH_RE.sub("<local-path>", text)
    text = POSIX_PATH_RE.sub("<local-path>", text)
    if len(text) > MAX_STRING_LENGTH:
        correlation_id = uuid.uuid4().hex
        text = f"{text[:MAX_STRING_LENGTH]}… [truncated correlation_id={correlation_id}]"
    return text


def sanitize_payload(value: Any, *, data_dir: Path | str | None = None) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            sanitized[text_key] = (
                REDACTED
                if is_sensitive_key(text_key)
                else sanitize_payload(item, data_dir=data_dir)
            )
        return sanitized
    if isinstance(value, tuple):
        return tuple(sanitize_payload(item, data_dir=data_dir) for item in value)
    if isinstance(value, list):
        return [sanitize_payload(item, data_dir=data_dir) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_payload(item, data_dir=data_dir) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, data_dir=data_dir)
    return value


def success_response(**payload: Any) -> dict[str, Any]:
    result = {"status": "success", "ok": True}
    result.update(payload)
    return sanitize_payload(result)


def error_response(
    error_type: str,
    message: str,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "status": "error",
        "ok": False,
        "error_type": error_type,
        "message": message,
        "retryable": retryable,
    }
    payload.update(extra)
    return sanitize_payload(payload)


def response_from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return sanitize_payload(exc.to_response())
    return error_response("api_error", str(exc), retryable=False)
