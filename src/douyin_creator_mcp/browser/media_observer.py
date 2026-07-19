"""Deterministic MediaBundle convergence independent from Playwright callbacks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..content.models import MediaBundle, MediaCandidate
from ..errors import MEDIA_BUNDLE_AMBIGUOUS, AppError


@dataclass(slots=True)
class BundleWindows:
    min_observe_ms: int = 1500
    multi_stable_ms: int = 350
    single_stable_ms: int = 750
    max_observe_ms: int = 6000


class MediaBundleCollector:
    def __init__(
        self,
        target_video_id: str,
        platform_video_id: str | None,
        parser_version: str,
        *,
        windows: BundleWindows | None = None,
    ):
        self.target_video_id = target_video_id
        self.platform_video_id = platform_video_id
        self.parser_version = parser_version
        self.windows = windows or BundleWindows()
        self._candidates: dict[str, MediaCandidate] = {}
        self._started_at: float | None = None
        self._first_candidate_at: float | None = None
        self._stable_since: float | None = None
        self._identity: tuple[str, str, str] | None = None

    def start(self, now: float) -> None:
        self._started_at = now

    def observe(self, candidate: MediaCandidate) -> None:
        if candidate.target_video_id != self.target_video_id:
            raise AppError(MEDIA_BUNDLE_AMBIGUOUS, "Observed candidate targets another video.")
        if (
            self.platform_video_id
            and candidate.platform_video_id
            and candidate.platform_video_id != self.platform_video_id
        ):
            raise AppError(MEDIA_BUNDLE_AMBIGUOUS, "Platform video identity changed.")
        identity = (
            candidate.browser_session_id,
            candidate.frame_id,
            candidate.playback_instance_id,
        )
        if self._identity is not None and identity != self._identity:
            raise AppError(MEDIA_BUNDLE_AMBIGUOUS, "Frame/playback identity changed.")
        self._identity = identity
        if candidate.candidate_id not in self._candidates:
            self._candidates[candidate.candidate_id] = candidate
            self._stable_since = candidate.observed_at_monotonic
            if self._first_candidate_at is None:
                self._first_candidate_at = candidate.observed_at_monotonic

    def convergence_reason(self, now: float) -> str | None:
        if self._started_at is None:
            raise RuntimeError("Collector has not started.")
        elapsed_ms = (now - self._started_at) * 1000
        if elapsed_ms >= self.windows.max_observe_ms:
            return "max_window"
        if not self._candidates or self._first_candidate_at is None or self._stable_since is None:
            return None
        if (now - self._first_candidate_at) * 1000 < self.windows.min_observe_ms:
            return None
        stable_ms = (now - self._stable_since) * 1000
        required = (
            self.windows.multi_stable_ms
            if len(self._candidates) >= 2
            else self.windows.single_stable_ms
        )
        return "stable_multi" if len(self._candidates) >= 2 and stable_ms >= required else (
            "stable_single" if len(self._candidates) == 1 and stable_ms >= required else None
        )

    def build(self, now: float) -> MediaBundle | None:
        reason = self.convergence_reason(now)
        if reason is None:
            return None
        return MediaBundle(
            bundle_id=uuid.uuid4().hex,
            target_video_id=self.target_video_id,
            platform_video_id=self.platform_video_id,
            candidates=tuple(
                sorted(self._candidates.values(), key=lambda item: item.candidate_id)
            ),
            convergence_reason=reason,
            parser_version=self.parser_version,
        )
