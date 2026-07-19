"""Immutable cross-module value objects; secrets intentionally resist serialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class SecretValue:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal_for_internal_use(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "[REDACTED]"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class EphemeralRequest:
    url: SecretValue
    headers: tuple[tuple[str, SecretValue], ...] = ()

    @classmethod
    def from_values(
        cls, url: str, headers: Mapping[str, str] | None = None
    ) -> EphemeralRequest:
        return cls(
            SecretValue(url),
            tuple((str(key), SecretValue(str(value))) for key, value in (headers or {}).items()),
        )

    def reveal_for_internal_use(self) -> tuple[str, dict[str, str]]:
        return (
            self.url.reveal_for_internal_use(),
            {key: value.reveal_for_internal_use() for key, value in self.headers},
        )

    def __repr__(self) -> str:
        return "[REDACTED]"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    candidate_id: str
    target_video_id: str
    platform_video_id: str | None
    browser_session_id: str
    page_id: str
    frame_id: str
    playback_instance_id: str
    observed_at_monotonic: float
    mime_hint: str | None
    bitrate_hint: int | None
    declared_duration_ms: int | None
    declared_bytes: int | None
    ephemeral_request: EphemeralRequest


@dataclass(frozen=True, slots=True)
class MediaBundle:
    bundle_id: str
    target_video_id: str
    platform_video_id: str | None
    candidates: tuple[MediaCandidate, ...]
    convergence_reason: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class MediaAsset:
    asset_id: str
    media_role: str
    storage_path: Path
    sha256: str
    size_bytes: int
    duration_ms: int | None
    container: str | None
    audio_codec: str | None
    video_codec: str | None
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True, slots=True)
class AsrSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class AsrResult:
    segments: tuple[AsrSegment, ...]
    language: str | None
    provider: str
    model: str
    model_version: str | None

    @property
    def raw_text(self) -> str:
        return "".join(segment.text for segment in self.segments)
