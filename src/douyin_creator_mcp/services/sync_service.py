"""Available data synchronization."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..errors import AppError
from ..storage.db import Database
from .account_service import AccountService
from .capability_service import CapabilityService


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SyncService:
    def __init__(
        self,
        db: Database,
        account_service: AccountService,
        capability_service: CapabilityService,
    ):
        self.db = db
        self.account_service = account_service
        self.capability_service = capability_service

    def _start_job(self, account_id: str, job_type: str) -> str:
        job_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO sync_jobs (id, account_id, job_type, status, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, account_id, job_type, "running", utc_now_iso()),
        )
        return job_id

    def _finish_job(
        self,
        job_id: str,
        status: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE sync_jobs
            SET status = ?, finished_at = ?, error_type = ?, error_message = ?
            WHERE id = ?
            """,
            (status, utc_now_iso(), error_type, error_message, job_id),
        )

    def sync_available_data(self, account_id: str) -> dict[str, Any]:
        job_id = self._start_job(account_id, "sync_available_data")
        synced: list[str] = []
        notes: list[str] = []
        try:
            self.account_service.get_account_profile(account_id)
            synced.append("account_profile")
        except AppError as exc:
            notes.append(f"Account profile sync skipped: {exc.error_type}")
        capabilities = self.capability_service.check_capabilities(account_id)
        synced.append("capabilities")
        status = "completed" if not notes else "partial"
        self._finish_job(job_id, status)
        return {
            "sync_job_id": job_id,
            "status": status,
            "synced": synced,
            "capabilities": capabilities["capabilities"],
            "analysis_notes": notes or ["Available data synchronization completed."],
        }
