"""Application errors and stable error type constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


AUTHORIZATION_REQUIRED = "authorization_required"
AUTHORIZATION_EXPIRED = "authorization_expired"
CAPABILITY_MISSING = "capability_missing"
SCOPE_MISSING = "scope_missing"
MCP_ACCESS_DENIED = "mcp_access_denied"
API_RATE_LIMITED = "api_rate_limited"
API_ERROR = "api_error"
NETWORK_ERROR = "network_error"
INVALID_RESPONSE = "invalid_response"
DATA_NOT_AVAILABLE = "data_not_available"
CONFIGURATION_ERROR = "configuration_error"
VALIDATION_ERROR = "validation_error"
PROFILE_IN_USE = "profile_in_use"
PARSER_DEGRADED = "parser_degraded"
VIDEO_IDENTITY_UNRESOLVED = "video_identity_unresolved"
ACCOUNT_MISMATCH = "account_mismatch"
ACCOUNT_IDENTITY_UNRESOLVED = "account_identity_unresolved"


@dataclass(slots=True)
class AppError(Exception):
    """Domain exception that can be serialized into MCP responses."""

    error_type: str
    message: str
    retryable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.error_type}: {self.message}"

    def to_response(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "ok": False,
            "error_type": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
        }
        payload.update(self.extra)
        return payload
