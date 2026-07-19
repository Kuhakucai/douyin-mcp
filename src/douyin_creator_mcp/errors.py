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
INSTANCE_IN_USE = "instance_in_use"
LEGACY_SCHEMA_UNRECOGNIZED = "legacy_schema_unrecognized"
MIGRATION_CHECKSUM_MISMATCH = "migration_checksum_mismatch"
CURRENT_SCHEMA_INVALID = "current_schema_invalid"
INVALID_CURSOR = "invalid_cursor"
TRANSCRIPT_REVISION_GONE = "transcript_revision_gone"
TRANSCRIPT_DISABLED = "transcript_disabled"
LEASE_LOST = "lease_lost"
MEDIA_REJECTED = "media_rejected"
MEDIA_BUNDLE_AMBIGUOUS = "media_bundle_ambiguous"
MEDIA_NO_AUDIO_TRACK = "media_no_audio_track"
PROCESS_FAILED = "process_failed"
ASR_FAILED = "asr_failed"
PROFILE_IN_USE = "profile_in_use"
PARSER_DEGRADED = "parser_degraded"
VIDEO_IDENTITY_UNRESOLVED = "video_identity_unresolved"
ACCOUNT_MISMATCH = "account_mismatch"
ACCOUNT_IDENTITY_UNRESOLVED = "account_identity_unresolved"
PLATFORM_TERMS_ACKNOWLEDGEMENT_REQUIRED = (
    "platform_terms_acknowledgement_required"
)


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


class RetryClass:
    """Stable retry classification persisted with failed jobs."""

    TRANSIENT = "transient"
    REQUIRES_USER = "requires_user"
    PERMANENT = "permanent"
