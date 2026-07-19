"""Transactional run/job/lease/asset/transcript repository."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..content.models import AsrResult, MediaAsset
from ..errors import DATA_NOT_AVAILABLE, LEASE_LOST, VALIDATION_ERROR, AppError
from ..responses import sanitize_text
from .db import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat()


class TranscriptRepository:
    def __init__(
        self,
        db: Database,
        *,
        pipeline_version: str = "transcript-v1",
        max_attempts: int = 2,
        lease_seconds: int = 30,
        data_dir: Path | str | None = None,
    ):
        self.db = db
        self.pipeline_version = pipeline_version
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.data_dir = Path(data_dir) if data_dir is not None else db.path.parent

    def create_run(
        self,
        account_id: str,
        video_ids: list[str],
        *,
        force: bool = False,
        trigger: str = "mcp",
        target_mode: str = "video_ids",
    ) -> dict[str, Any]:
        if not video_ids:
            raise AppError(VALIDATION_ERROR, "At least one video_id is required.")
        if len(video_ids) > 100:
            raise AppError(VALIDATION_ERROR, "At most 100 video_ids may be submitted.")
        video_ids = list(dict.fromkeys(video_ids))
        run_id = uuid.uuid4().hex
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            placeholders = ",".join("?" for _ in video_ids)
            rows = conn.execute(
                f"SELECT id FROM videos WHERE account_id=? AND is_active=1 "
                f"AND id IN ({placeholders})",
                (account_id, *video_ids),
            ).fetchall()
            found = {str(row["id"]) for row in rows}
            missing = [video_id for video_id in video_ids if video_id not in found]
            if missing:
                raise AppError(
                    DATA_NOT_AVAILABLE,
                    "Some videos are missing, inactive, or belong to another account.",
                    extra={"missing_video_ids": missing},
                )
            conn.execute(
                "INSERT INTO video_transcript_runs("
                "id,account_id,trigger,target_mode,requested_video_ids_json,target_state,"
                "lifecycle_state,result,pipeline_version,cancel_requested,created_at,started_at,updated_at"
                ") VALUES(?,?,?,?,?,'frozen','queued',NULL,?,0,?,?,?)",
                (
                    run_id,
                    account_id,
                    trigger,
                    target_mode,
                    json.dumps(video_ids),
                    self.pipeline_version,
                    now,
                    now,
                    now,
                ),
            )
            for video_id in video_ids:
                job = conn.execute(
                    "SELECT * FROM video_content_jobs WHERE video_id=? AND pipeline_version=? "
                    "AND status IN ('queued','running','waiting_retry','waiting_user') "
                    "ORDER BY created_at LIMIT 1",
                    (video_id, self.pipeline_version),
                ).fetchone()
                cache = None
                if job is None and not force:
                    cache = conn.execute(
                        "SELECT status FROM video_transcripts "
                        "WHERE video_id=? AND is_current=1",
                        (video_id,),
                    ).fetchone()
                if job is None:
                    job_id = uuid.uuid4().hex
                    completed = cache is not None
                    stage = (
                        "no_speech"
                        if completed and cache["status"] == "no_speech"
                        else "analysis_ready" if completed else "registered"
                    )
                    conn.execute(
                        "INSERT INTO video_content_jobs("
                        "id,video_id,account_id,pipeline_version,force_requested,status,stage,"
                        "attempt_count,max_attempts,cancel_requested,created_at,finished_at,updated_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?)",
                        (
                            job_id,
                            video_id,
                            account_id,
                            self.pipeline_version,
                            int(force),
                            "completed" if completed else "queued",
                            stage,
                            0,
                            self.max_attempts,
                            0,
                            now,
                            now if completed else None,
                            now,
                        ),
                    )
                    job = conn.execute(
                        "SELECT * FROM video_content_jobs WHERE id=?", (job_id,)
                    ).fetchone()
                elif force and not bool(job["force_requested"]):
                    conn.execute(
                        "UPDATE video_content_jobs SET force_requested=1,updated_at=? WHERE id=?",
                        (now, job["id"]),
                    )
                terminal = str(job["status"]) == "completed"
                outcome = str(job["stage"]) if terminal else "pending"
                conn.execute(
                    "INSERT INTO video_transcript_run_items("
                    "run_id,job_id,video_id,demand_state,requested_force,outcome,"
                    "attached_at,completed_at"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        job["id"],
                        video_id,
                        "completed" if terminal else "active",
                        int(force),
                        outcome,
                        now,
                        now if terminal else None,
                    ),
                )
            self._refresh_run(conn, run_id, now)
        return self.get_run(run_id)

    def get_run(
        self,
        run_id: str,
        *,
        item_limit: int | None = None,
        after: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM video_transcript_runs WHERE id=?", (run_id,), read_only=True
        )
        if row is None:
            raise AppError(DATA_NOT_AVAILABLE, "Transcript run was not found.")
        all_items = self.db.query_all(
            "SELECT i.video_id,i.job_id,i.demand_state,i.outcome,j.status,j.stage,"
            "j.attempt_count,j.error_type,j.error_message "
            "FROM video_transcript_run_items i JOIN video_content_jobs j ON j.id=i.job_id "
            "WHERE i.run_id=? ORDER BY i.attached_at,i.video_id",
            (run_id,),
            read_only=True,
        )
        items = all_items
        has_more = False
        next_item = None
        if item_limit is not None:
            if not 1 <= item_limit <= 100:
                raise AppError(VALIDATION_ERROR, "item_limit must be between 1 and 100.")
            sql = (
                "SELECT i.video_id,i.job_id,i.attached_at,i.demand_state,i.outcome,"
                "j.status,j.stage,j.attempt_count,j.error_type,j.error_message "
                "FROM video_transcript_run_items i JOIN video_content_jobs j ON j.id=i.job_id "
                "WHERE i.run_id=?"
            )
            params: list[Any] = [run_id]
            if after:
                sql += (
                    " AND (i.attached_at>? OR (i.attached_at=? AND i.video_id>?) OR "
                    "(i.attached_at=? AND i.video_id=? AND i.job_id>?))"
                )
                params.extend(
                    [after[0], after[0], after[1], after[0], after[1], after[2]]
                )
            sql += " ORDER BY i.attached_at,i.video_id,i.job_id LIMIT ?"
            params.append(item_limit + 1)
            page = self.db.query_all(sql, tuple(params), read_only=True)
            has_more = len(page) > item_limit
            items = page[:item_limit]
            if has_more and items:
                last = items[-1]
                next_item = {
                    "attached_at": last["attached_at"],
                    "video_id": last["video_id"],
                    "job_id": last["job_id"],
                }
        result = {
            **self._public_run(row),
            "counts": self._counts(all_items),
            "items": items,
            "has_more": has_more,
            "next_cursor": None,
        }
        if next_item:
            result["_next_item"] = next_item
        return result

    def list_runs_page(
        self,
        account_id: str,
        limit: int = 20,
        before: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise AppError(VALIDATION_ERROR, "limit must be between 1 and 100.")
        sql = "SELECT * FROM video_transcript_runs WHERE account_id=?"
        params: list[Any] = [account_id]
        if before:
            sql += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend([before[0], before[0], before[1]])
        sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
        params.append(limit + 1)
        rows = self.db.query_all(sql, tuple(params), read_only=True)
        has_more = len(rows) > limit
        public = [self._public_run(row) for row in rows[:limit]]
        return {
            "runs": public,
            "has_more": has_more,
            "_next_run": (
                {"created_at": public[-1]["created_at"], "id": public[-1]["id"]}
                if has_more and public
                else None
            ),
        }

    def list_runs(
        self, account_id: str, limit: int = 20, before: str | None = None
    ) -> list[dict[str, Any]]:
        marker = (before, "") if before else None
        return self.list_runs_page(account_id, limit, marker)["runs"]

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT * FROM video_transcript_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise AppError(DATA_NOT_AVAILABLE, "Transcript run was not found.")
            if run["lifecycle_state"] == "terminal":
                return self.get_run(run_id)
            conn.execute(
                "UPDATE video_transcript_runs SET cancel_requested=1,"
                "lifecycle_state='cancelling',updated_at=? WHERE id=?",
                (now, run_id),
            )
            jobs = conn.execute(
                "SELECT job_id FROM video_transcript_run_items "
                "WHERE run_id=? AND demand_state='active'",
                (run_id,),
            ).fetchall()
            conn.execute(
                "UPDATE video_transcript_run_items SET demand_state='cancelled',"
                "outcome='cancelled',detached_at=?,completed_at=? "
                "WHERE run_id=? AND demand_state='active'",
                (now, now, run_id),
            )
            for row in jobs:
                active = conn.execute(
                    "SELECT 1 FROM video_transcript_run_items i "
                    "JOIN video_transcript_runs r ON r.id=i.run_id "
                    "WHERE i.job_id=? AND i.demand_state='active' "
                    "AND r.lifecycle_state NOT IN ('cancelling','terminal') LIMIT 1",
                    (row["job_id"],),
                ).fetchone()
                if active is None:
                    conn.execute(
                        "UPDATE video_content_jobs SET cancel_requested=1,updated_at=? "
                        "WHERE id=? AND status IN ('queued','running','waiting_retry','waiting_user')",
                        (now, row["job_id"]),
                    )
            self._refresh_run(conn, run_id, now)
        return self.get_run(run_id)

    def retry_run(self, old_run_id: str) -> dict[str, Any]:
        now = iso()
        retry_videos: list[str] = []
        account_id = ""
        with self.db.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT account_id FROM video_transcript_runs WHERE id=?", (old_run_id,)
            ).fetchone()
            if run is None:
                raise AppError(DATA_NOT_AVAILABLE, "Transcript run was not found.")
            account_id = str(run["account_id"])
            rows = conn.execute(
                "SELECT i.video_id,i.job_id,i.outcome,i.demand_state,j.status "
                "FROM video_transcript_run_items i JOIN video_content_jobs j ON j.id=i.job_id "
                "WHERE i.run_id=? ORDER BY i.attached_at,i.video_id",
                (old_run_id,),
            ).fetchall()
            retry_videos.extend(
                str(row["video_id"]) for row in rows if row["outcome"] == "failed"
            )
            waiting_jobs = {
                str(row["job_id"]): str(row["video_id"])
                for row in rows
                if row["demand_state"] == "active" and row["status"] == "waiting_user"
            }
            for job_id, video_id in waiting_jobs.items():
                changed = conn.execute(
                    "UPDATE video_content_jobs SET status='failed',retry_class='requires_user',"
                    "error_type=COALESCE(error_type,'resumed_by_retry'),"
                    "error_message=COALESCE(error_message,'Explicit retry requested.'),"
                    "finished_at=?,updated_at=? WHERE id=? AND status='waiting_user'",
                    (now, now, job_id),
                )
                if changed.rowcount != 1:
                    raise AppError(
                        VALIDATION_ERROR,
                        "Waiting job changed before the retry could be resumed.",
                        retryable=True,
                    )
                retry_videos.append(video_id)
                conn.execute(
                    "UPDATE video_transcript_run_items SET demand_state='completed',"
                    "outcome='failed',completed_at=?,"
                    "error_type=COALESCE(error_type,'resumed_by_retry'),"
                    "error_message=COALESCE(error_message,'Explicit retry requested.') "
                    "WHERE job_id=? AND demand_state='active'",
                    (now, job_id),
                )
                affected = conn.execute(
                    "SELECT DISTINCT run_id FROM video_transcript_run_items WHERE job_id=?",
                    (job_id,),
                ).fetchall()
                for item in affected:
                    self._refresh_run(conn, str(item["run_id"]), now)
        retry_videos = list(dict.fromkeys(retry_videos))
        if not retry_videos:
            raise AppError(VALIDATION_ERROR, "Run has no failed videos to retry.")
        return self.create_run(
            account_id, retry_videos, force=False, trigger=f"retry:{old_run_id}"
        )

    def claim_job(self, owner: str) -> dict[str, Any] | None:
        now_dt = utc_now()
        now, expires = iso(now_dt), iso(now_dt + timedelta(seconds=self.lease_seconds))
        token = secrets.token_urlsafe(24)
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT j.id FROM video_content_jobs j "
                "WHERE j.cancel_requested=0 AND ("
                "j.status='queued' OR "
                "(j.status='waiting_retry' AND (j.next_attempt_at IS NULL OR j.next_attempt_at<=?)) OR "
                "(j.status='running' AND j.lease_expires_at<?)) "
                "AND EXISTS(SELECT 1 FROM video_transcript_run_items i "
                "JOIN video_transcript_runs r ON r.id=i.run_id "
                "WHERE i.job_id=j.id AND i.demand_state='active' "
                "AND r.lifecycle_state NOT IN ('cancelling','terminal')) "
                "ORDER BY j.created_at,j.id LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            result = conn.execute(
                "UPDATE video_content_jobs SET status='running',lease_owner=?,lease_token=?,"
                "lease_expires_at=?,heartbeat_at=?,attempt_count=attempt_count+1,"
                "started_at=COALESCE(started_at,?),updated_at=? "
                "WHERE id=? AND (status!='running' OR lease_expires_at<?)",
                (owner, token, expires, now, now, now, row["id"], now),
            )
            if result.rowcount != 1:
                return None
            job = conn.execute(
                "SELECT * FROM video_content_jobs WHERE id=?", (row["id"],)
            ).fetchone()
        return dict(job)

    def heartbeat(self, job_id: str, token: str) -> bool:
        now_dt = utc_now()
        with self.db.transaction(immediate=True) as conn:
            result = conn.execute(
                "UPDATE video_content_jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=? "
                "WHERE id=? AND status='running' AND lease_token=?",
                (
                    iso(now_dt),
                    iso(now_dt + timedelta(seconds=self.lease_seconds)),
                    iso(now_dt),
                    job_id,
                    token,
                ),
            )
            return result.rowcount == 1

    def set_stage(self, job_id: str, token: str, stage: str, **fields: Any) -> None:
        allowed = {"bundle_id", "transcription_asset_id", "reference_video_asset_id"}
        if set(fields) - allowed:
            raise ValueError("Unsupported stage fields.")
        assignments = ["stage=?", "updated_at=?"]
        params: list[Any] = [stage, iso()]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            params.append(value)
        params.extend([job_id, token])
        with self.db.transaction(immediate=True) as conn:
            result = conn.execute(
                f"UPDATE video_content_jobs SET {','.join(assignments)} "
                "WHERE id=? AND status='running' AND lease_token=?",
                tuple(params),
            )
            if result.rowcount != 1:
                raise AppError(LEASE_LOST, "Job lease is no longer owned by this worker.")

    def fail_job(
        self,
        job_id: str,
        token: str,
        error_type: str,
        message: str,
        retry_class: str,
    ) -> None:
        now = iso()
        safe_message = sanitize_text(message, data_dir=self.data_dir)
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT attempt_count,max_attempts FROM video_content_jobs "
                "WHERE id=? AND status='running' AND lease_token=?",
                (job_id, token),
            ).fetchone()
            if row is None:
                raise AppError(LEASE_LOST, "Job lease is no longer owned by this worker.")
            retry = retry_class == "transient" and row["attempt_count"] < row["max_attempts"]
            status = "waiting_retry" if retry else (
                "waiting_user" if retry_class == "requires_user" else "failed"
            )
            next_attempt = (
                iso(utc_now() + timedelta(seconds=2 ** int(row["attempt_count"])))
                if retry
                else None
            )
            conn.execute(
                "UPDATE video_content_jobs SET status=?,retry_class=?,next_attempt_at=?,"
                "error_type=?,error_message=?,lease_owner=NULL,lease_token=NULL,"
                "lease_expires_at=NULL,heartbeat_at=NULL,finished_at=?,updated_at=? "
                "WHERE id=? AND lease_token=?",
                (
                    status,
                    retry_class,
                    next_attempt,
                    error_type,
                    safe_message,
                    now if status == "failed" else None,
                    now,
                    job_id,
                    token,
                ),
            )
            if status == "failed":
                conn.execute(
                    "UPDATE video_transcript_run_items SET demand_state='completed',"
                    "outcome='failed',completed_at=?,error_type=?,error_message=? "
                    "WHERE job_id=? AND demand_state='active'",
                    (now, error_type, safe_message, job_id),
                )
            run_ids = conn.execute(
                "SELECT DISTINCT run_id FROM video_transcript_run_items WHERE job_id=?",
                (job_id,),
            ).fetchall()
            for run in run_ids:
                self._refresh_run(conn, str(run["run_id"]), now)

    def recover_expired(self) -> int:
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id FROM video_content_jobs WHERE status='running' "
                "AND lease_expires_at<?",
                (now,),
            ).fetchall()
            for row in rows:
                active = conn.execute(
                    "SELECT 1 FROM video_transcript_run_items WHERE job_id=? "
                    "AND demand_state='active' LIMIT 1",
                    (row["id"],),
                ).fetchone()
                conn.execute(
                    "UPDATE video_content_jobs SET status=?,lease_owner=NULL,lease_token=NULL,"
                    "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?",
                    ("queued" if active else "cancelled", now, row["id"]),
                )
        return len(rows)

    def job_cancel_requested(self, job_id: str, token: str) -> bool:
        row = self.db.query_one(
            "SELECT cancel_requested FROM video_content_jobs "
            "WHERE id=? AND status='running' AND lease_token=?",
            (job_id, token),
            read_only=True,
        )
        return row is None or bool(row["cancel_requested"])

    def release_owner(self, owner: str) -> int:
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id,cancel_requested FROM video_content_jobs "
                "WHERE status='running' AND lease_owner=?",
                (owner,),
            ).fetchall()
            for row in rows:
                active = conn.execute(
                    "SELECT 1 FROM video_transcript_run_items WHERE job_id=? "
                    "AND demand_state='active' LIMIT 1",
                    (row["id"],),
                ).fetchone()
                status = (
                    "cancelled"
                    if bool(row["cancel_requested"]) or active is None
                    else "queued"
                )
                conn.execute(
                    "UPDATE video_content_jobs SET status=?,lease_owner=NULL,lease_token=NULL,"
                    "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?",
                    (status, now, row["id"]),
                )
        return len(rows)

    def finalize_cancelled(self, job_id: str, token: str) -> bool:
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            result = conn.execute(
                "UPDATE video_content_jobs SET status='cancelled',"
                "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                "finished_at=?,updated_at=? WHERE id=? AND status='running' "
                "AND lease_token=? AND cancel_requested=1",
                (now, now, job_id, token),
            )
            return result.rowcount == 1

    def add_media_asset(
        self,
        job_id: str,
        token: str,
        asset: MediaAsset,
        bundle_id: str,
        *,
        is_transcription_source: bool = False,
    ) -> None:
        now = iso()
        relative = asset.storage_path.resolve().relative_to(self.data_dir.resolve())
        with self.db.transaction(immediate=True) as conn:
            owned = conn.execute(
                "SELECT 1 FROM video_content_jobs WHERE id=? AND status='running' "
                "AND lease_token=?",
                (job_id, token),
            ).fetchone()
            if owned is None:
                raise AppError(LEASE_LOST, "Job lease is no longer owned by this worker.")
            job = conn.execute(
                "SELECT video_id,account_id FROM video_content_jobs WHERE id=?", (job_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO video_media_assets("
                "id,bundle_id,video_id,account_id,job_id,media_role,state,storage_path,"
                "sha256,size_bytes,duration_ms,container,audio_codec,video_codec,sample_rate,"
                "channels,is_transcription_source,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?, 'available',?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset.asset_id,
                    bundle_id,
                    job["video_id"],
                    job["account_id"],
                    job_id,
                    asset.media_role,
                    relative.as_posix(),
                    asset.sha256,
                    asset.size_bytes,
                    asset.duration_ms,
                    asset.container,
                    asset.audio_codec,
                    asset.video_codec,
                    asset.sample_rate,
                    asset.channels,
                    int(is_transcription_source),
                    now,
                    now,
                ),
            )
            if is_transcription_source:
                conn.execute(
                    "UPDATE video_content_jobs SET transcription_asset_id=?,bundle_id=?,"
                    "updated_at=? WHERE id=? AND lease_token=?",
                    (asset.asset_id, bundle_id, now, job_id, token),
                )

    def select_media_asset(self, job_id: str, token: str, asset_id: str) -> None:
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            asset = conn.execute(
                "SELECT id FROM video_media_assets WHERE id=? AND job_id=? "
                "AND state='available' AND media_role IN ('audio_only','audiovisual')",
                (asset_id, job_id),
            ).fetchone()
            if asset is None:
                raise AppError(DATA_NOT_AVAILABLE, "Transcription asset is unavailable.")
            owned = conn.execute(
                "SELECT 1 FROM video_content_jobs WHERE id=? AND status='running' "
                "AND lease_token=?",
                (job_id, token),
            ).fetchone()
            if owned is None:
                raise AppError(LEASE_LOST, "Job lease is no longer owned by this worker.")
            conn.execute(
                "UPDATE video_media_assets SET is_transcription_source=0,updated_at=? "
                "WHERE job_id=?",
                (now, job_id),
            )
            conn.execute(
                "UPDATE video_media_assets SET is_transcription_source=1,updated_at=? "
                "WHERE id=?",
                (now, asset_id),
            )
            conn.execute(
                "UPDATE video_content_jobs SET transcription_asset_id=?,updated_at=? "
                "WHERE id=? AND lease_token=?",
                (asset_id, now, job_id, token),
            )

    def commit_transcript(
        self,
        job_id: str,
        token: str,
        asset_id: str | None,
        result: AsrResult,
        *,
        extractor_version: str,
        params: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        segments = list(result.segments)
        for expected, segment in enumerate(segments):
            if (
                segment.index != expected
                or segment.start_ms < 0
                or segment.end_ms < segment.start_ms
                or not segment.text
            ):
                raise AppError(VALIDATION_ERROR, "ASR segments are not continuous and valid.")
        raw_text = result.raw_text
        status = "available" if segments else "no_speech"
        transcript_id = uuid.uuid4().hex
        now = iso()
        with self.db.transaction(immediate=True) as conn:
            job = conn.execute(
                "SELECT * FROM video_content_jobs WHERE id=? AND status='running' "
                "AND lease_token=? AND cancel_requested=0",
                (job_id, token),
            ).fetchone()
            if job is None:
                raise AppError(LEASE_LOST, "Job lease is no longer owned by this worker.")
            row = conn.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS revision FROM video_transcripts "
                "WHERE video_id=?",
                (job["video_id"],),
            ).fetchone()
            revision = int(row["revision"])
            conn.execute(
                "UPDATE video_transcripts SET is_current=0 "
                "WHERE video_id=? AND is_current=1",
                (job["video_id"],),
            )
            conn.execute(
                "INSERT INTO video_transcripts("
                "id,video_id,account_id,job_id,asset_id,revision,is_current,status,provider,"
                "model,model_version,extractor_version,params_json,language,raw_text,text_sha256,"
                "segment_count,duration_ms,created_at"
                ") VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transcript_id,
                    job["video_id"],
                    job["account_id"],
                    job_id,
                    asset_id,
                    revision,
                    status,
                    result.provider,
                    result.model,
                    result.model_version,
                    extractor_version,
                    json.dumps(params or {}, sort_keys=True),
                    result.language,
                    raw_text,
                    hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    len(segments),
                    duration_ms,
                    now,
                ),
            )
            for segment in segments:
                conn.execute(
                    "INSERT INTO video_transcript_segments("
                    "id,transcript_id,segment_index,start_ms,end_ms,text,avg_logprob,"
                    "no_speech_prob,language) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        transcript_id,
                        segment.index,
                        segment.start_ms,
                        segment.end_ms,
                        segment.text,
                        segment.avg_logprob,
                        segment.no_speech_prob,
                        segment.language,
                    ),
                )
            stage = "analysis_ready" if segments else "no_speech"
            updated = conn.execute(
                "UPDATE video_content_jobs SET status='completed',stage=?,"
                "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                "finished_at=?,updated_at=? WHERE id=? AND status='running' "
                "AND lease_token=? AND cancel_requested=0",
                (stage, now, now, job_id, token),
            )
            if updated.rowcount != 1:
                raise AppError(LEASE_LOST, "Job lease was lost during transcript commit.")
            conn.execute(
                "UPDATE video_transcript_run_items SET demand_state='completed',outcome=?,"
                "completed_at=? WHERE job_id=? AND demand_state='active'",
                (stage, now, job_id),
            )
            run_ids = conn.execute(
                "SELECT DISTINCT run_id FROM video_transcript_run_items WHERE job_id=?",
                (job_id,),
            ).fetchall()
            for run in run_ids:
                self._refresh_run(conn, str(run["run_id"]), now)
        return {
            "transcript_id": transcript_id,
            "revision": revision,
            "status": status,
            "segment_count": len(segments),
        }

    @staticmethod
    def _counts(items: Iterable[dict[str, Any] | sqlite3.Row]) -> dict[str, int]:
        counts = {
            "total": 0,
            "ready": 0,
            "no_speech": 0,
            "failed": 0,
            "cancelled": 0,
            "active": 0,
        }
        for item in items:
            counts["total"] += 1
            outcome = str(item["outcome"])
            if outcome == "analysis_ready":
                counts["ready"] += 1
            elif outcome in counts:
                counts[outcome] += 1
            if item["demand_state"] == "active":
                counts["active"] += 1
        return counts

    @classmethod
    def _refresh_run(cls, conn: sqlite3.Connection, run_id: str, now: str) -> None:
        items = conn.execute(
            "SELECT i.demand_state,i.outcome,j.status "
            "FROM video_transcript_run_items i JOIN video_content_jobs j ON j.id=i.job_id "
            "WHERE i.run_id=?",
            (run_id,),
        ).fetchall()
        counts = cls._counts(items)
        run = conn.execute(
            "SELECT cancel_requested FROM video_transcript_runs WHERE id=?", (run_id,)
        ).fetchone()
        if counts["active"]:
            active_statuses = {
                str(item["status"])
                for item in items
                if item["demand_state"] == "active"
            }
            state = (
                "waiting_user"
                if active_statuses and active_statuses <= {"waiting_user"}
                else "running"
            )
            result, finished = None, None
        else:
            state, finished = "terminal", now
            if run and bool(run["cancel_requested"]):
                result = "cancelled"
            elif counts["failed"] and counts["failed"] == counts["total"]:
                result = "failed"
            elif counts["failed"] or counts["cancelled"]:
                result = "partial"
            else:
                result = "success"
        conn.execute(
            "UPDATE video_transcript_runs SET lifecycle_state=?,result=?,finished_at=?,"
            "updated_at=? WHERE id=?",
            (state, result, finished, now, run_id),
        )

    @staticmethod
    def _public_run(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "id",
                "account_id",
                "trigger",
                "target_mode",
                "target_state",
                "lifecycle_state",
                "result",
                "pipeline_version",
                "cancel_requested",
                "created_at",
                "started_at",
                "finished_at",
                "updated_at",
                "error_type",
                "error_message",
            )
        }
