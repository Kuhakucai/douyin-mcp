"""Hybrid transcript-ingestion policy for sync warmup and analysis demand."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from ..config import Settings
from ..errors import DATA_NOT_AVAILABLE, TRANSCRIPT_DISABLED, VALIDATION_ERROR, AppError
from ..storage.db import Database
from ..storage.transcripts import TranscriptRepository
from .browser_service import BROWSER_DEFAULT_ACCOUNT_ID
from .transcript_coordinator import TranscriptCoordinator


class TranscriptIngestionPolicy:
    """Choose what to transcribe without blocking list synchronization."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        repository: TranscriptRepository,
        coordinator: TranscriptCoordinator,
    ):
        self.settings = settings
        self.db = db
        self.repository = repository
        self.coordinator = coordinator

    def capture_sync_state(
        self, account_id: str = BROWSER_DEFAULT_ACCOUNT_ID
    ) -> dict[str, Any]:
        rows = self.db.query_all(
            "SELECT id FROM videos WHERE account_id=? AND is_active=1",
            (account_id,),
            read_only=True,
        )
        activity = self.db.query_one(
            "SELECT 1 AS found FROM video_transcript_runs "
            "WHERE account_id=? LIMIT 1",
            (account_id,),
            read_only=True,
        )
        return {
            "video_ids": {str(row["id"]) for row in rows},
            "has_transcript_activity": activity is not None,
        }

    def after_creator_sync(
        self,
        before: dict[str, Any],
        sync_result: dict[str, Any],
        *,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        if not self.settings.transcript_ingestion_enabled:
            return self._idle("disabled")
        if sync_result.get("login_status") not in {None, "logged_in"}:
            return self._idle("login_not_ready")
        if sync_result.get("status") not in {"completed", "cache_hit"}:
            return self._idle("sync_not_complete")

        rows = self._missing_public_videos(account_id)
        selected: list[str] = []
        trigger = "sync_idle"
        deferred = 0
        if (
            self.settings.transcript_auto_warmup_enabled
            and not bool(before.get("has_transcript_activity"))
        ):
            selected = [
                str(row["id"])
                for row in rows[: self.settings.transcript_warmup_recent_limit]
            ]
            deferred = max(0, len(rows) - len(selected))
            trigger = "initial_warmup"
        elif self.settings.transcript_auto_ingest_new_videos:
            previous = {str(value) for value in before.get("video_ids") or set()}
            new_rows = [row for row in rows if str(row["id"]) not in previous]
            selected = [
                str(row["id"])
                for row in new_rows[: self.settings.transcript_auto_new_video_limit]
            ]
            deferred = max(0, len(new_rows) - len(selected))
            trigger = "new_public_video"

        if not selected:
            return self._idle(trigger)
        run = self.repository.create_run(
            account_id,
            selected,
            force=False,
            trigger=trigger,
            target_mode="recent" if trigger == "initial_warmup" else "video_ids",
        )
        self.coordinator.wake()
        return self._run_summary(run, trigger, deferred)

    def prepare_analysis(
        self,
        video_ids: list[str],
        *,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        if not 1 <= len(video_ids) <= 20 or len(set(video_ids)) != len(video_ids):
            raise AppError(
                VALIDATION_ERROR,
                "Analysis preparation requires between 1 and 20 unique video_ids.",
            )
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.query_all(
            f"SELECT id,visibility,content_kind FROM videos "
            f"WHERE account_id=? AND is_active=1 AND id IN ({placeholders})",
            (account_id, *video_ids),
            read_only=True,
        )
        by_id = {str(row["id"]): row for row in rows}
        missing = [video_id for video_id in video_ids if video_id not in by_id]
        if missing:
            raise AppError(
                DATA_NOT_AVAILABLE,
                "Some requested videos are missing, inactive, or belong to another account.",
                extra={"missing_video_ids": missing},
            )
        rejected = [
            video_id
            for video_id in video_ids
            if by_id[video_id].get("visibility") != "public"
            or by_id[video_id].get("content_kind") != "video"
        ]
        if rejected:
            raise AppError(
                VALIDATION_ERROR,
                "Only currently public video content can be prepared for analysis.",
                extra={"rejected_video_ids": rejected},
            )
        transcript_rows = self.db.query_all(
            f"SELECT video_id FROM video_transcripts "
            f"WHERE is_current=1 AND video_id IN ({placeholders})",
            tuple(video_ids),
            read_only=True,
        )
        ready = {str(row["video_id"]) for row in transcript_rows}
        pending = [video_id for video_id in video_ids if video_id not in ready]
        if not pending:
            return {
                "readiness": "analysis_ready",
                "ready_video_ids": video_ids,
                "pending_video_ids": [],
                "run_id": None,
            }
        if not self.settings.transcript_ingestion_enabled:
            raise AppError(
                TRANSCRIPT_DISABLED,
                "Transcript ingestion is disabled by TRANSCRIPT_INGESTION_ENABLED.",
            )
        run = self.repository.create_run(
            account_id,
            pending,
            force=False,
            trigger="analysis_demand",
            target_mode="video_ids",
        )
        self.coordinator.wake()
        return {
            "readiness": "preparing",
            "ready_video_ids": [
                video_id for video_id in video_ids if video_id in ready
            ],
            "pending_video_ids": pending,
            "run_id": run["id"],
            "run_lifecycle_state": run["lifecycle_state"],
            "run_result": run["result"],
            "counts": run["counts"],
            "poll_tool": "douyin_browser_get_transcript_run",
        }

    def backfill_plan(
        self, *, account_id: str = BROWSER_DEFAULT_ACCOUNT_ID
    ) -> dict[str, Any]:
        videos = self.db.query_all(
            "SELECT v.id,v.duration,t.id AS transcript_id "
            "FROM videos v "
            "LEFT JOIN video_transcripts t ON t.video_id=v.id AND t.is_current=1 "
            "WHERE v.account_id=? AND v.is_active=1 "
            "AND v.visibility='public' AND v.content_kind='video' "
            "ORDER BY v.publish_time DESC,v.id",
            (account_id,),
            read_only=True,
        )
        pending = [row for row in videos if row.get("transcript_id") is None]
        job_rows = self.db.query_all(
            "SELECT started_at,finished_at FROM video_content_jobs "
            "WHERE account_id=? AND status='completed' "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL",
            (account_id,),
            read_only=True,
        )
        elapsed_samples: list[float] = []
        for row in job_rows:
            try:
                elapsed = (
                    datetime.fromisoformat(str(row["finished_at"]))
                    - datetime.fromisoformat(str(row["started_at"]))
                ).total_seconds()
            except (TypeError, ValueError):
                continue
            if elapsed > 0:
                elapsed_samples.append(elapsed)
        asset_rows = self.db.query_all(
            "SELECT size_bytes,duration_ms FROM video_media_assets "
            "WHERE account_id=? AND state='available' AND size_bytes>0",
            (account_id,),
            read_only=True,
        )
        byte_rates = [
            float(row["size_bytes"]) / (float(row["duration_ms"]) / 1000)
            for row in asset_rows
            if row.get("duration_ms") and float(row["duration_ms"]) > 0
        ]
        asset_sizes = [
            int(row["size_bytes"]) for row in asset_rows if row.get("size_bytes")
        ]
        known_duration_seconds = sum(
            int(row["duration"]) for row in pending if row.get("duration") is not None
        )
        unknown_duration_count = sum(
            row.get("duration") is None for row in pending
        )
        median_seconds = median(elapsed_samples) if elapsed_samples else None
        estimated_seconds = (
            round(median_seconds * len(pending)) if median_seconds is not None else None
        )
        estimated_storage = None
        if byte_rates or asset_sizes:
            known_bytes = (
                median(byte_rates) * known_duration_seconds if byte_rates else 0
            )
            unknown_bytes = (
                median(asset_sizes) * unknown_duration_count if asset_sizes else 0
            )
            estimated_storage = round(known_bytes + unknown_bytes)
        return {
            "status": "ready",
            "public_video_count": len(videos),
            "ready_video_count": len(videos) - len(pending),
            "pending_video_count": len(pending),
            "pending_duration_seconds": known_duration_seconds,
            "unknown_duration_count": unknown_duration_count,
            "estimated_processing_seconds": estimated_seconds,
            "estimated_processing_range_seconds": (
                [
                    max(1, round(estimated_seconds * 0.75)),
                    max(1, round(estimated_seconds * 1.5)),
                ]
                if estimated_seconds is not None
                else None
            ),
            "estimated_additional_storage_bytes": estimated_storage,
            "estimate_sample_count": len(elapsed_samples),
            "worker_count": self.settings.transcript_worker_count,
            "per_run_limit": 100,
            "requires_explicit_confirmation": True,
            "submit_tool": "douyin_browser_submit_transcript_run",
            "submit_arguments": {"all_public": True, "force": False},
        }

    def _missing_public_videos(self, account_id: str) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT v.id,v.publish_time FROM videos v "
            "LEFT JOIN video_transcripts t ON t.video_id=v.id AND t.is_current=1 "
            "WHERE v.account_id=? AND v.is_active=1 "
            "AND v.visibility='public' AND v.content_kind='video' "
            "AND t.id IS NULL "
            "ORDER BY v.publish_time DESC,v.id",
            (account_id,),
            read_only=True,
        )

    @staticmethod
    def _idle(reason: str) -> dict[str, Any]:
        return {
            "status": "idle",
            "trigger": reason,
            "selected_count": 0,
            "deferred_count": 0,
            "run_id": None,
        }

    @staticmethod
    def _run_summary(
        run: dict[str, Any], trigger: str, deferred_count: int
    ) -> dict[str, Any]:
        return {
            "status": "scheduled",
            "trigger": trigger,
            "selected_count": int(run["counts"]["total"]),
            "deferred_count": deferred_count,
            "run_id": run["id"],
            "lifecycle_state": run["lifecycle_state"],
            "result": run["result"],
            "counts": run["counts"],
        }
