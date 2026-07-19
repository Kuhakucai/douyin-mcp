"""FastMCP lifespan-managed service container."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from .browser.executor import BrowserExecutor
from .config import Settings, ensure_runtime_dirs
from .content.asr import AudioExtractor, ControlledProcessRunner, FasterWhisperTranscriber
from .content.media import FFprobe, SecureMediaDownloader
from .instance_lock import InstanceLock
from .services.browser_service import BrowserService
from .services.transcript_coordinator import DefaultPipelineAdapter, TranscriptCoordinator
from .services.transcript_policy import TranscriptIngestionPolicy
from .services.transcript_query import CursorSigner, TranscriptQueryService
from .storage.db import Database
from .storage.transcripts import TranscriptRepository


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    db: Database
    browser_service: BrowserService
    browser_executor: BrowserExecutor
    transcript_repository: TranscriptRepository
    transcript_query: TranscriptQueryService
    transcript_coordinator: TranscriptCoordinator
    transcript_policy: TranscriptIngestionPolicy


class Runtime:
    """Constructed without I/O; all state changes happen inside ``lifespan``."""

    def __init__(
        self,
        settings: Settings,
        *,
        browser_executor_factory: Any | None = None,
        coordinator_adapter_factory: Any | None = None,
    ):
        self.settings = settings
        self.browser_executor_factory = browser_executor_factory
        self.coordinator_adapter_factory = coordinator_adapter_factory
        self.instance_lock = InstanceLock(settings.data_dir)
        self.container: ServiceContainer | None = None

    @asynccontextmanager
    async def lifespan(self, server: Any) -> AsyncIterator[dict[str, Any]]:
        del server
        self.instance_lock.acquire()
        try:
            ensure_runtime_dirs(self.settings)
            db = Database(self.settings.data_dir / "douyin.sqlite")
            db.init_schema()
            signer = CursorSigner.load_or_create(
                self.settings.data_dir / ".cursor-hmac.key"
            )
            browser_executor = (
                self.browser_executor_factory(self.settings)
                if self.browser_executor_factory
                else BrowserExecutor(self.settings, database=db)
            )
            browser_executor.start()
            repository = TranscriptRepository(
                db,
                pipeline_version=self.settings.transcript_pipeline_version,
                max_attempts=self.settings.transcript_max_attempts,
                lease_seconds=self.settings.transcript_lease_seconds,
                data_dir=self.settings.data_dir,
            )
            query = TranscriptQueryService(
                db,
                signer,
                response_max_bytes=self.settings.transcript_response_max_bytes,
            )
            if self.coordinator_adapter_factory:
                adapter = self.coordinator_adapter_factory(
                    self.settings, browser_executor, repository
                )
            else:
                runner = ControlledProcessRunner()
                probe = FFprobe(
                    runner,
                    self.settings.transcript_ffprobe_path,
                    self.settings.transcript_process_timeout_seconds,
                )
                downloader = SecureMediaDownloader(
                    self.settings.data_dir,
                    max_bytes=self.settings.transcript_media_max_bytes,
                    min_free_bytes=self.settings.transcript_media_min_free_bytes,
                )
                extractor = AudioExtractor(
                    runner,
                    self.settings.transcript_ffmpeg_path,
                    self.settings.transcript_process_timeout_seconds,
                )
                transcriber = FasterWhisperTranscriber(
                    self.settings.transcript_asr_model_dir
                    or self.settings.data_dir / "models" / self.settings.transcript_asr_model_size,
                    model_size=self.settings.transcript_asr_model_size,
                    device=self.settings.transcript_asr_device,
                    compute_type=self.settings.transcript_asr_compute_type,
                )
                adapter = DefaultPipelineAdapter(
                    browser_executor=browser_executor,
                    downloader=downloader,
                    probe=probe,
                    audio_extractor=extractor,
                    transcriber=transcriber,
                    data_dir=self.settings.data_dir,
                )
            coordinator = TranscriptCoordinator(
                repository,
                adapter,
                worker_count=self.settings.transcript_worker_count,
                heartbeat_seconds=self.settings.transcript_heartbeat_seconds,
            )
            transcript_policy = TranscriptIngestionPolicy(
                self.settings,
                db,
                repository,
                coordinator,
            )
            self.container = ServiceContainer(
                settings=self.settings,
                db=db,
                browser_service=BrowserService(
                    self.settings, db, browser_executor=browser_executor
                ),
                browser_executor=browser_executor,
                transcript_repository=repository,
                transcript_query=query,
                transcript_coordinator=coordinator,
                transcript_policy=transcript_policy,
            )
            if self.settings.transcript_ingestion_enabled:
                coordinator.start()
            yield {"services": self.container}
        finally:
            container, self.container = self.container, None
            if container is not None:
                container.transcript_coordinator.stop()
                container.browser_service.close_browser()
                container.browser_executor.shutdown()
                container.db.checkpoint()
            self.instance_lock.release()

    def require_container(self) -> ServiceContainer:
        if self.container is None:
            raise RuntimeError("Runtime lifespan is not active.")
        return self.container
