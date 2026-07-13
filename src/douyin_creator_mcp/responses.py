"""Response helpers and sensitive field filtering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import AppError

SENSITIVE_NAME_PARTS = ("token", "secret", "code", "authorization")
REDACTED = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_NAME_PARTS)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            sanitized[text_key] = REDACTED if is_sensitive_key(text_key) else sanitize_payload(item)
        return sanitized
    if isinstance(value, tuple):
        return tuple(sanitize_payload(item) for item in value)
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_payload(item) for item in value]
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
