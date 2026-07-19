"""Background coordinator for persistent transcript jobs."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from ..browser.commands import ObserveMediaBundle, VerifyAccount
from ..browser.executor import BrowserExecutor
from ..content.asr import AudioExtractor, FasterWhisperTranscriber
from ..content.media import FFprobe, SecureMediaDownloader, select_transcription_asset
from ..content.models import MediaAsset, MediaBundle
from ..errors import (
    CAPABILITY_MISSING,
    LEASE_LOST,
    MEDIA_NO_AUDIO_TRACK,
    AppError,
    RetryClass,
)
from ..logging_utils import structured_event
from ..storage.transcripts import TranscriptRepository

logger = logging.getLogger(__name__)


class PipelineAdapter(Protocol):
    def process(
        self,
        job: dict[str, Any],
        token: str,
        repository: TranscriptRepository,
        cancelled: Any,
    ) -> None: ...


class LeaseGuard:
    def __init__(
        self,
        repository: TranscriptRepository,
        job_id: str,
        token: str,
        *,
        interval_seconds: float,
    ):
        self.repository = repository
        self.job_id = job_id
        self.token = token
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(self.interval_seconds + 1)

    def _run(self) -> None:
        failures = 0
        while not self._stop.wait(self.interval_seconds):
            try:
                ok = self.repository.heartbeat(self.job_id, self.token)
            except Exception:
                ok = False
            failures = 0 if ok else failures + 1
            if failures >= 2:
                self._lost.set()
                return


class DefaultPipelineAdapter:
    def __init__(
        self,
        *,
        browser_executor: BrowserExecutor,
        downloader: SecureMediaDownloader,
        probe: FFprobe,
        audio_extractor: AudioExtractor,
        transcriber: FasterWhisperTranscriber,
        data_dir: Path,
    ):
        self.browser_executor = browser_executor
        self.downloader = downloader
        self.probe = probe
        self.audio_extractor = audio_extractor
        self.transcriber = transcriber
        self.data_dir = data_dir

    def process(
        self,
        job: dict[str, Any],
        token: str,
        repository: TranscriptRepository,
        cancelled: Any,
    ) -> None:
        repository.set_stage(job["id"], token, "target_verified")
        expected = repository.db.query_one(
            "SELECT id,item_id,video_id,title,publish_time,visibility,content_kind "
            "FROM videos WHERE id=? AND account_id=?",
            (job["video_id"], job["account_id"]),
            read_only=True,
        )
        if expected is None:
            raise AppError(MEDIA_NO_AUDIO_TRACK, "Registered video no longer exists.")
        self.browser_executor.execute(
            VerifyAccount(
                account_id=str(job["account_id"]),
                target_video_id=str(job["video_id"]),
                expected_video=expected,
            )
        )
        repository.set_stage(job["id"], token, "observing_bundle")
        bundle = self.browser_executor.execute(
            ObserveMediaBundle(
                account_id=str(job["account_id"]),
                target_video_id=str(job["video_id"]),
            )
        )
        if not isinstance(bundle, MediaBundle):
            raise TypeError("ObserveMediaBundle must return MediaBundle.")
        assets = self._download_bundle(bundle, job, token, repository, cancelled)
        if not any(item.media_role in {"audio_only", "audiovisual"} for item in assets):
            second = self.browser_executor.execute(
                ObserveMediaBundle(
                    account_id=str(job["account_id"]),
                    target_video_id=str(job["video_id"]),
                    full_window=True,
                )
            )
            if not isinstance(second, MediaBundle):
                raise TypeError("ObserveMediaBundle must return MediaBundle.")
            assets.extend(self._download_bundle(second, job, token, repository, cancelled))
        selected = select_transcription_asset(assets)
        repository.select_media_asset(job["id"], token, selected.asset_id)
        repository.set_stage(
            job["id"],
            token,
            "extracting_audio",
            transcription_asset_id=selected.asset_id,
            bundle_id=bundle.bundle_id,
        )
        wav = self.data_dir / "staging" / f"{job['id']}-{token[:8]}.wav"
        try:
            self.audio_extractor.extract(selected.storage_path, wav, cancelled=cancelled)
            repository.set_stage(job["id"], token, "transcribing")
            title_row = repository.db.query_one(
                "SELECT title FROM videos WHERE id=?",
                (job["video_id"],),
                read_only=True,
            ) or {}
            result = self.transcriber.transcribe(
                wav, title=str(title_row.get("title") or ""), cancelled=cancelled
            )
            repository.set_stage(job["id"], token, "persisting")
            repository.commit_transcript(
                job["id"],
                token,
                selected.asset_id,
                result,
                extractor_version="ffmpeg-pcm16k-v1",
            )
        finally:
            wav.unlink(missing_ok=True)

    def _download_bundle(
        self,
        bundle: MediaBundle,
        job: dict[str, Any],
        token: str,
        repository: TranscriptRepository,
        cancelled: Any,
    ) -> list[MediaAsset]:
        repository.set_stage(job["id"], token, "downloading_candidates")
        assets: list[MediaAsset] = []
        ordered = sorted(
            bundle.candidates,
            key=lambda item: (
                item.declared_bytes if item.declared_bytes is not None else 2**63,
                item.candidate_id,
            ),
        )
        for candidate in ordered:
            if cancelled():
                raise AppError(LEASE_LOST, "Job was cancelled or its lease was lost.")
            path = (
                self.data_dir
                / "media"
                / str(job["video_id"])
                / str(job["id"])
                / f"{candidate.candidate_id}.media"
            )
            digest, size, detail = self.downloader.download(
                candidate.ephemeral_request,
                path,
                probe=self.probe.probe,
                cancelled=cancelled,
            )
            asset = MediaAsset(
                uuid.uuid4().hex,
                detail.media_role,
                path,
                digest,
                size,
                detail.duration_ms,
                detail.container,
                detail.audio_codec,
                detail.video_codec,
                detail.sample_rate,
                detail.channels,
            )
            if detail.media_role == "video_only":
                path.unlink(missing_ok=True)
                continue
            repository.add_media_asset(job["id"], token, asset, bundle.bundle_id)
            assets.append(asset)
        return assets


class TranscriptCoordinator:
    def __init__(
        self,
        repository: TranscriptRepository,
        adapter: PipelineAdapter,
        *,
        runtime_id: str | None = None,
        worker_count: int = 1,
        heartbeat_seconds: float = 5,
        poll_seconds: float = 0.2,
    ):
        self.repository = repository
        self.adapter = adapter
        self.runtime_id = runtime_id or uuid.uuid4().hex
        self.worker_count = worker_count
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self.repository.recover_expired()
        self._stop.clear()
        for index in range(self.worker_count):
            thread = threading.Thread(
                target=self._worker,
                name=f"transcript-worker-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self.repository.release_owner(self.runtime_id)
        self._threads.clear()

    def _worker(self) -> None:
        while not self._stop.is_set():
            job = self.repository.claim_job(self.runtime_id)
            if job is None:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
                continue
            token = str(job["lease_token"])
            guard = LeaseGuard(
                self.repository,
                str(job["id"]),
                token,
                interval_seconds=self.heartbeat_seconds,
            )
            guard.start()
            cancelled = lambda: (
                self._stop.is_set()
                or guard.lost
                or self.repository.job_cancel_requested(str(job["id"]), token)
            )
            try:
                structured_event(
                    logger,
                    "transcript_job_started",
                    job_id=job["id"],
                    video_id=job["video_id"],
                    stage=job["stage"],
                    pipeline_version=job["pipeline_version"],
                )
                self.adapter.process(job, token, self.repository, cancelled)
            except AppError as exc:
                if self.repository.job_cancel_requested(str(job["id"]), token):
                    self.repository.finalize_cancelled(str(job["id"]), token)
                    continue
                retry_class = (
                    RetryClass.REQUIRES_USER
                    if exc.error_type in {CAPABILITY_MISSING}
                    else RetryClass.TRANSIENT if exc.retryable else RetryClass.PERMANENT
                )
                try:
                    self.repository.fail_job(
                        str(job["id"]), token, exc.error_type, exc.message, retry_class
                    )
                except AppError as lease_exc:
                    if lease_exc.error_type != LEASE_LOST:
                        raise
            except Exception as exc:
                try:
                    self.repository.fail_job(
                        str(job["id"]),
                        token,
                        exc.__class__.__name__,
                        str(exc),
                        RetryClass.PERMANENT,
                    )
                except AppError as lease_exc:
                    if lease_exc.error_type != LEASE_LOST:
                        raise
            finally:
                guard.stop()
