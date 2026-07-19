"""Single-account browser data channel and trustworthy local analytics."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import uuid
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ..browser.extractors import (
    DETAIL_METRIC_FIELDS,
    LOGGED_IN,
    LOGIN_REQUIRED,
    VERIFICATION_REQUIRED,
    collect_all_video_cards,
    detail_video_id_from_url,
    extract_detail_metrics,
    extract_page_snapshot,
)
from ..browser.commands import LoginStart, LoginStatus, SyncCreatorList, SyncVideoDetails
from ..browser.executor import BrowserExecutor
from ..browser.profile_lock import ProfileLock
from ..browser.session import BrowserSession
from ..compliance import (
    platform_compliance_status,
    require_platform_risk_acknowledgement,
)
from ..config import Settings
from ..errors import (
    ACCOUNT_IDENTITY_UNRESOLVED,
    ACCOUNT_MISMATCH,
    DATA_NOT_AVAILABLE,
    VALIDATION_ERROR,
    VIDEO_IDENTITY_UNRESOLVED,
    AppError,
)
from ..storage.db import Database
from .metrics import FORMULA_VERSION, compute_derived_metrics, percentile_rank


BROWSER_DEFAULT_ACCOUNT_ID = "browser-default"
LIST_SOURCE = "browser_list"
DETAIL_SOURCE = "browser_detail"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BrowserService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        session_factory: Callable[[], BrowserSession] | None = None,
        browser_executor: BrowserExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._session_factory = session_factory
        self._session: BrowserSession | None = None
        self._active_profile_lock: ProfileLock | None = None
        self._browser_executor = browser_executor

    # Login and status -------------------------------------------------

    def login_start(self) -> dict[str, Any]:
        require_platform_risk_acknowledgement(self.settings.data_dir)
        if self._browser_executor is not None:
            result = self._browser_executor.execute(LoginStart(headless=False))
            return {
                **result,
                "message": "浏览器已打开。如页面要求登录或验证，请完成扫码后继续。",
            }
        try:
            with self._browser_access():
                session = self._get_session(headless=False)
                page = session.open_creator_home()
                snapshot = extract_page_snapshot(page)
                return {
                    "browser_running": session.is_running,
                    "login_status": snapshot["login_status"],
                    "title": snapshot["title"],
                    "source_url": snapshot["source_url"],
                    "message": "浏览器已打开。如页面要求登录或验证，请完成扫码后继续。",
                }
        except Exception:
            self.close_browser()
            raise

    def login_status(self) -> dict[str, Any]:
        if self._browser_executor is not None:
            result = self._browser_executor.execute(LoginStatus())
            result.setdefault(
                "message",
                "浏览器会话尚未启动。需要登录时调用登录工具。"
                if not result.get("browser_running")
                else "浏览器会话正在运行。",
            )
            return result
        if self._session is None or not self._session.is_running:
            return {
                "browser_running": False,
                "login_status": "not_started",
                "message": "浏览器会话尚未启动。需要登录时调用登录工具。",
            }
        pages = getattr(self._session.context, "pages", None) or []
        if not pages:
            return {
                "browser_running": True,
                "login_status": "unknown",
                "message": "浏览器正在运行，但当前没有可检查页面。",
            }
        snapshot = extract_page_snapshot(pages[0])
        return {
            "browser_running": True,
            "login_status": snapshot["login_status"],
            "title": snapshot["title"],
            "source_url": snapshot["source_url"],
            "video_candidate_count": len(snapshot["video_candidates"]),
        }

    def get_status(self) -> dict[str, Any]:
        latest_job = self.db.query_one(
            "SELECT * FROM sync_jobs ORDER BY started_at DESC LIMIT 1"
        )
        active_job = self.db.query_one(
            "SELECT * FROM sync_jobs WHERE status = 'running' "
            "ORDER BY started_at DESC LIMIT 1"
        )
        list_snapshot = self._latest_metric_snapshot(source=LIST_SOURCE)
        detail_snapshot = self._latest_metric_snapshot(source=DETAIL_SOURCE)
        browser_snapshot = self.db.query_one(
            "SELECT status, created_at FROM browser_snapshots ORDER BY created_at DESC LIMIT 1"
        )
        coverage = self.get_metric_coverage()
        account_binding = self._public_account_binding(
            self.db.query_one(
                "SELECT account_id, anchor_count, created_at, last_verified_at "
                "FROM browser_account_bindings WHERE account_id = ?",
                (BROWSER_DEFAULT_ACCOUNT_ID,),
            )
        )
        return {
            "status": "completed",
            "platform_compliance": platform_compliance_status(
                self.settings.data_dir
            ),
            "login_status": browser_snapshot["status"] if browser_snapshot else "unknown",
            "last_login_check_at": browser_snapshot["created_at"] if browser_snapshot else None,
            "list_freshness": self._freshness(
                list_snapshot.get("captured_at") if list_snapshot else None,
                self.settings.douyin_list_cache_ttl_hours,
            ),
            "detail_freshness": self._freshness(
                detail_snapshot.get("captured_at") if detail_snapshot else None,
                self.settings.douyin_detail_cache_ttl_hours,
            ),
            "latest_sync": self._safe_job(latest_job),
            "active_sync": self._safe_job(active_job),
            "profile_lock": self._public_profile_lock(self._profile_lock().inspect()),
            "account_binding": account_binding,
            "coverage": coverage["coverage"],
            "warnings": coverage["warnings"],
        }

    def sync_if_needed(
        self,
        scope: str = "list",
        max_age_hours: int | None = None,
        mode: str = "background_first",
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        if scope not in {"list", "details", "all"}:
            raise AppError(VALIDATION_ERROR, "scope must be list, details, or all.")
        self._validate_mode(mode)
        if (
            max_age_hours is not None
            and (
                not isinstance(max_age_hours, int)
                or isinstance(max_age_hours, bool)
                or max_age_hours < 0
            )
        ):
            raise AppError(VALIDATION_ERROR, "max_age_hours must be a non-negative integer.")
        results: dict[str, Any] = {}
        warnings: list[str] = []
        if scope in {"list", "all"}:
            ttl = self.settings.douyin_list_cache_ttl_hours if max_age_hours is None else max_age_hours
            latest = self._latest_metric_snapshot(source=LIST_SOURCE)
            freshness = self._freshness(latest.get("captured_at") if latest else None, ttl)
            if not freshness["is_stale"]:
                results["list"] = {"status": "cache_hit", "freshness": freshness}
            else:
                results["list"] = self.sync_creator_data(mode=mode)
        if scope in {"details", "all"}:
            ttl = self.settings.douyin_detail_cache_ttl_hours if max_age_hours is None else max_age_hours
            selected = self._select_detail_videos(
                BROWSER_DEFAULT_ACCOUNT_ID, None, recent_limit
            )
            stale_ids = [
                video["id"]
                for video in selected
                if self._freshness(
                    (self._latest_metric_snapshot(video["id"], DETAIL_SOURCE) or {}).get(
                        "captured_at"
                    ),
                    ttl,
                )["is_stale"]
            ]
            if selected and not stale_ids:
                latest = self._latest_metric_snapshot(source=DETAIL_SOURCE)
                results["details"] = {
                    "status": "cache_hit",
                    "freshness": self._freshness(latest.get("captured_at"), ttl),
                    "covered_video_count": len(selected),
                }
            else:
                results["details"] = self.sync_video_details(
                    video_ids=stale_ids or None,
                    recent_limit=recent_limit,
                    force=max_age_hours is not None,
                    batch_size=self.settings.douyin_detail_batch_size,
                    mode=mode,
                )
        if mode == "background_first":
            warnings.append("后台模式失败或触发验证时，将要求用户在可见浏览器中重新扫码。")
        statuses = {str(item.get("status")) for item in results.values()}
        status = "cache_hit" if statuses == {"cache_hit"} else (
            "partial" if "partial" in statuses or "user_action_required" in statuses else "completed"
        )
        return {
            "status": status,
            "scope": scope,
            "results": results,
            "warnings": warnings,
            "next_action": self._next_action_from_results(results),
        }

    # List synchronization --------------------------------------------

    def sync_creator_data(
        self,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
        mode: str = "visible",
        force: bool = False,
    ) -> dict[str, Any]:
        require_platform_risk_acknowledgement(self.settings.data_dir)
        self._validate_mode(mode)
        del force  # A full list sync is already idempotent at the video identity layer.
        job_id = self._start_job(
            account_id, "browser_sync_creator_data", self.settings.douyin_list_parser_version
        )
        snapshot: dict[str, Any] | None = None
        captured_at = utc_now_iso()
        try:
            access = self._browser_access() if self._browser_executor is None else nullcontext()
            with access:
                if self._browser_executor is not None:
                    browser_result = self._browser_executor.execute(
                        SyncCreatorList(headless=mode == "background_first")
                    )
                    snapshot = browser_result["snapshot"]
                    structured_videos = browser_result["videos"]
                    load_stats = browser_result["load_stats"]
                    page = None
                else:
                    session = self._get_session(headless=mode == "background_first")
                    page = session.open_creator_video_page()
                    snapshot = extract_page_snapshot(page)
                if mode == "background_first" and snapshot["login_status"] in {
                    LOGIN_REQUIRED,
                    VERIFICATION_REQUIRED,
                }:
                    self.close_browser()
                    self.login_start()
                    if self._browser_executor is not None:
                        browser_result = self._browser_executor.execute(
                            SyncCreatorList(headless=False)
                        )
                        snapshot = browser_result["snapshot"]
                        structured_videos = browser_result["videos"]
                        load_stats = browser_result["load_stats"]
                    else:
                        pages = getattr(self._session.context, "pages", None) or []
                        snapshot = extract_page_snapshot(pages[0]) if pages else snapshot
                if self._browser_executor is None:
                    load_stats = {
                        "initial_card_count": 0,
                        "current_dom_card_count": 0,
                        "loaded_card_count": 0,
                        "page_total_video_count": None,
                        "scroll_rounds": 0,
                        "stop_reason": "not_logged_in",
                    }
                    structured_videos = []
                    if snapshot["login_status"] == LOGGED_IN:
                        structured_videos, load_stats = collect_all_video_cards(page)
                        snapshot = extract_page_snapshot(
                            page,
                            structured_videos=structured_videos,
                            load_stats=load_stats,
                        )
                if snapshot["login_status"] == LOGGED_IN:
                    account_identity = self._verify_browser_account_identity(
                        account_id,
                        structured_videos,
                        captured_at,
                    )
                else:
                    account_identity = {"status": "not_verified", "bound": False}
                snapshot_id = self._save_snapshot(account_id, snapshot)
                job_status = self._job_status_from_login_status(snapshot["login_status"])
                videos_upserted = 0
                metric_snapshots = 0
                if snapshot["login_status"] == LOGGED_IN:
                    videos_upserted, metric_snapshots = self._upsert_structured_videos(
                        account_id, structured_videos, job_id, captured_at
                    )
                    declared = load_stats.get("page_total_video_count")
                    loaded = int(load_stats.get("loaded_card_count") or 0)
                    parsed = len(structured_videos)
                    if parsed == 0 and declared != 0:
                        job_status = "partial"
                    elif declared is not None and (loaded < int(declared) or parsed < int(declared)):
                        job_status = "partial"
                coverage = self._list_coverage(structured_videos)
                progress = {
                    "completed": len(structured_videos),
                    "total": load_stats.get("page_total_video_count"),
                    "phase": "list_sync",
                }
                self._finish_job(job_id, job_status, progress=progress, coverage=coverage)
                warnings = self._sync_notes(snapshot["login_status"], job_status)
                return {
                    "sync_job_id": job_id,
                    "snapshot_id": snapshot_id,
                    "status": job_status,
                    "login_status": snapshot["login_status"],
                    "title": snapshot["title"],
                    "source_url": snapshot["source_url"],
                    "video_candidate_count": len(snapshot["video_candidates"]),
                    "structured_video_count": len(structured_videos),
                    "parsed_video_count": len(structured_videos),
                    "declared_video_count": load_stats.get("page_total_video_count"),
                    "page_total_video_count": load_stats.get("page_total_video_count"),
                    "loaded_video_count": load_stats.get("loaded_card_count"),
                    "current_dom_card_count": load_stats.get("current_dom_card_count"),
                    "videos_upserted": videos_upserted,
                    "metrics_upserted": metric_snapshots,
                    "captured_at": captured_at,
                    "parser_version": self.settings.douyin_list_parser_version,
                    "coverage": coverage,
                    "freshness": self._freshness(captured_at, self.settings.douyin_list_cache_ttl_hours),
                    "warnings": warnings,
                    "analysis_notes": warnings,
                    "account_identity": account_identity,
                    "next_action": self._login_next_action(snapshot["login_status"]),
                }
        except Exception as exc:
            self._finish_job(
                job_id,
                "failed",
                error_type=exc.error_type if isinstance(exc, AppError) else exc.__class__.__name__,
                error_message=str(exc),
            )
            raise
        finally:
            keep_open = snapshot and snapshot.get("login_status") in {
                LOGIN_REQUIRED,
                VERIFICATION_REQUIRED,
            }
            if self.settings.douyin_browser_auto_close and not keep_open:
                self.close_browser()

    # Detail synchronization ------------------------------------------

    def sync_video_details(
        self,
        video_ids: list[str] | None = None,
        recent_limit: int = 20,
        force: bool = False,
        batch_size: int | None = None,
        cursor: int = 0,
        mode: str = "visible",
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        require_platform_risk_acknowledgement(self.settings.data_dir)
        self._validate_mode(mode)
        if video_ids is not None and not 1 <= len(video_ids) <= 50:
            raise AppError(VALIDATION_ERROR, "video_ids must contain between 1 and 50 ids.")
        if not isinstance(recent_limit, int) or isinstance(recent_limit, bool) or not 1 <= recent_limit <= 50:
            raise AppError(VALIDATION_ERROR, "recent_limit must be between 1 and 50.")
        selected_batch_size = self.settings.douyin_detail_batch_size if batch_size is None else batch_size
        if not isinstance(selected_batch_size, int) or isinstance(selected_batch_size, bool) or not 1 <= selected_batch_size <= 10:
            raise AppError(VALIDATION_ERROR, "batch_size must be between 1 and 10.")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise AppError(VALIDATION_ERROR, "cursor must be a non-negative integer.")

        videos = self._select_detail_videos(account_id, video_ids, recent_limit)
        if not videos:
            raise AppError(DATA_NOT_AVAILABLE, "No synchronized videos are available for detail sync.", True)
        if cursor >= len(videos):
            raise AppError(VALIDATION_ERROR, "cursor is outside the selected video range.")

        job_id = self._start_job(
            account_id, "browser_sync_video_details", self.settings.douyin_detail_parser_version
        )
        end = min(len(videos), cursor + selected_batch_size)
        batch = videos[cursor:end]
        succeeded = 0
        partial = 0
        cached = 0
        failures: list[dict[str, Any]] = []
        field_hits = {field: 0 for field in DETAIL_METRIC_FIELDS}
        captured_at: str | None = None
        login_status = LOGGED_IN
        try:
            access = self._browser_access() if self._browser_executor is None else nullcontext()
            with access:
                session = (
                    self._get_session(headless=mode == "background_first")
                    if self._browser_executor is None
                    else None
                )
                for index, video in enumerate(batch, start=cursor):
                    if not force and self._has_fresh_detail(video["id"]):
                        cached += 1
                        cached_snapshot = self._latest_metric_snapshot(
                            video["id"], DETAIL_SOURCE
                        ) or {}
                        cached_at = cached_snapshot.get("captured_at")
                        if cached_at and (captured_at is None or str(cached_at) > captured_at):
                            captured_at = str(cached_at)
                        for field in DETAIL_METRIC_FIELDS:
                            if cached_snapshot.get(field) is not None:
                                field_hits[field] += 1
                        continue
                    try:
                        had_detail_url = bool(video.get("video_url"))
                        if self._browser_executor is not None:
                            browser_result = self._browser_executor.execute(
                                SyncVideoDetails(
                                    videos=(dict(video),),
                                    headless=mode == "background_first",
                                )
                            )
                            item = browser_result["details"][0]
                            self._bind_video_detail_identity(
                                video,
                                str(item["source_url"]),
                                require_platform_id=not bool(item["had_detail_url"]),
                            )
                            detail = item["detail"]
                        elif had_detail_url:
                            page = session.open_video_detail(str(video["video_url"]))
                            self._bind_video_detail_identity(
                                video,
                                str(getattr(page, "url", "") or ""),
                                require_platform_id=False,
                            )
                            detail = extract_detail_metrics(page, video)
                        else:
                            page = session.open_video_detail_from_list(
                                str(video.get("title") or ""),
                                int(video["publish_time"]),
                            )
                            self._bind_video_detail_identity(
                                video,
                                str(getattr(page, "url", "") or ""),
                                require_platform_id=True,
                            )
                            detail = extract_detail_metrics(page, video)
                    except AppError as exc:
                        failure = {
                            "video_id": video["id"],
                            "reason": exc.error_type,
                            "message": exc.message,
                        }
                        failure.update(exc.extra)
                        failures.append(failure)
                        continue
                    except Exception as exc:
                        failures.append(
                            {"video_id": video["id"], "reason": "navigation_failed", "message": str(exc)}
                        )
                        continue
                    login_status = detail["login_status"]
                    if login_status in {LOGIN_REQUIRED, VERIFICATION_REQUIRED}:
                        if mode == "background_first":
                            self.close_browser()
                            visible_login = self.login_start()
                            login_status = str(visible_login["login_status"])
                        return_status = (
                            "user_action_required"
                            if login_status in {LOGIN_REQUIRED, VERIFICATION_REQUIRED}
                            else "partial"
                        )
                        progress = {
                            "completed": index,
                            "total": len(videos),
                            "phase": "detail_sync",
                        }
                        self._finish_job(
                            job_id,
                            return_status,
                            progress=progress,
                            resume_cursor=index,
                            coverage=self._field_coverage(
                                field_hits, max(1, succeeded + partial + cached)
                            ),
                        )
                        return self._detail_sync_result(
                            job_id,
                            return_status,
                            len(videos),
                            cursor,
                            index,
                            succeeded,
                            partial,
                            cached,
                            failures,
                            field_hits,
                            captured_at,
                            login_status,
                        )
                    if not detail["identity_confirmed"]:
                        failures.append({"video_id": video["id"], "reason": "video_identity_unresolved"})
                        continue
                    if detail["quality"] == "parser_degraded":
                        failures.append({"video_id": video["id"], "reason": "parser_degraded"})
                        continue
                    metric_captured_at = utc_now_iso()
                    snapshot_id = self._save_metric_snapshot(
                        job_id,
                        video["id"],
                        account_id,
                        DETAIL_SOURCE,
                        detail["metrics"],
                        detail["raw_metrics"],
                        detail["missing_reasons"],
                        detail["quality"],
                        self.settings.douyin_detail_parser_version,
                        captured_at=metric_captured_at,
                    )
                    captured_at = metric_captured_at
                    self._save_derived_metrics(snapshot_id, detail["metrics"])
                    for field, value in detail["metrics"].items():
                        if field in field_hits and value is not None:
                            field_hits[field] += 1
                    if detail["quality"] == "complete":
                        succeeded += 1
                    else:
                        partial += 1

                next_cursor = end if end < len(videos) else None
                status = "completed"
                if failures or partial or next_cursor is not None:
                    status = "partial"
                coverage = self._field_coverage(
                    field_hits, max(1, succeeded + partial + cached)
                )
                progress = {"completed": end, "total": len(videos), "phase": "detail_sync"}
                self._finish_job(
                    job_id,
                    status,
                    progress=progress,
                    coverage=coverage,
                    resume_cursor=next_cursor,
                )
                return self._detail_sync_result(
                    job_id,
                    status,
                    len(videos),
                    cursor,
                    next_cursor,
                    succeeded,
                    partial,
                    cached,
                    failures,
                    field_hits,
                    captured_at,
                    login_status,
                )
        except Exception as exc:
            self._finish_job(
                job_id, "failed", error_type=exc.__class__.__name__, error_message=str(exc)
            )
            raise
        finally:
            if self.settings.douyin_browser_auto_close and login_status not in {
                LOGIN_REQUIRED,
                VERIFICATION_REQUIRED,
            }:
                self.close_browser()

    # Query and analysis ----------------------------------------------

    def list_videos(
        self,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
        limit: int = 20,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        sort: str = "publish_time_desc",
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AppError(VALIDATION_ERROR, "limit must be an integer between 1 and 100.")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise AppError(VALIDATION_ERROR, "offset must be a non-negative integer.")
        order_by = {
            "publish_time_desc": "v.publish_time DESC, v.id ASC",
            "publish_time_asc": "v.publish_time ASC, v.id ASC",
            "play_count_desc": "COALESCE(s.play_count, 0) DESC, v.publish_time DESC",
            "title_asc": "v.title ASC, v.id ASC",
        }.get(sort)
        if order_by is None:
            raise AppError(VALIDATION_ERROR, "Unsupported sort value.")
        where = ["v.account_id = ?", "v.source = 'browser_dom'"]
        params: list[Any] = [account_id]
        if filters and filters.get("status"):
            where.append("v.status = ?")
            params.append(str(filters["status"]))
        where_sql = " AND ".join(where)
        total_row = self.db.query_one(
            f"SELECT COUNT(*) AS count FROM videos AS v WHERE {where_sql}", tuple(params)
        )
        rows = self.db.query_all(
            f"""
            SELECT
              v.id, v.account_id, v.item_id, v.title, v.publish_time, v.cover_url,
              v.video_url, v.duration, v.status, v.source, v.updated_at,
              s.captured_at, s.play_count, s.like_count, s.comment_count,
              s.share_count, s.collect_count, s.quality, s.parser_version,
              d.like_rate, d.collect_rate, d.comment_rate, d.share_rate,
              d.play_rate, d.interaction_rate, d.formula_version
            FROM videos AS v
            LEFT JOIN video_metric_snapshots AS s ON s.id = (
              SELECT latest.id FROM video_metric_snapshots AS latest
              WHERE latest.video_id = v.id AND latest.source = '{LIST_SOURCE}'
              ORDER BY latest.captured_at DESC, latest.rowid DESC LIMIT 1
            )
            LEFT JOIN video_derived_metrics AS d ON d.snapshot_id = s.id
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple([*params, limit, offset]),
        )
        videos = [self._video_row_to_result(row) for row in rows]
        captured_at = max(
            (str(row["captured_at"]) for row in rows if row.get("captured_at")),
            default=None,
        )
        return {
            "account_id": account_id,
            "total": int(total_row["count"] if total_row else 0),
            "limit": limit,
            "offset": offset,
            "videos": videos,
            "freshness": self._freshness(captured_at, self.settings.douyin_list_cache_ttl_hours),
            "warnings": [] if videos else ["本地尚无作品数据，请先同步作品列表。"],
        }

    def get_video_performance(
        self,
        video_id: str,
        period: str = "30d",
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        video = self.db.query_one(
            "SELECT * FROM videos WHERE id = ? AND account_id = ?",
            (video_id, account_id),
        )
        if not video:
            raise AppError(DATA_NOT_AVAILABLE, "Video was not found in the local cache.", False)
        snapshots = self.db.query_all(
            f"""
            SELECT s.*, d.like_rate, d.collect_rate, d.comment_rate, d.share_rate,
                   d.play_rate, d.interaction_rate, d.formula_version
            FROM video_metric_snapshots AS s
            LEFT JOIN video_derived_metrics AS d ON d.snapshot_id = s.id
            WHERE s.video_id = ? {self._period_sql(period, 's.captured_at')}
            ORDER BY s.captured_at DESC, s.rowid DESC
            LIMIT 100
            """,
            tuple([video_id, *self._period_params(period)]),
        )
        parsed = [self._snapshot_row(row) for row in snapshots]
        latest_by_source: dict[str, dict[str, Any]] = {}
        for row in parsed:
            latest_by_source.setdefault(str(row["source"]), row)
        latest = latest_by_source.get(DETAIL_SOURCE) or latest_by_source.get(LIST_SOURCE)
        return {
            "status": "completed",
            "period": period,
            "video": self._public_video(video),
            "latest_list_snapshot": latest_by_source.get(LIST_SOURCE),
            "latest_detail_snapshot": latest_by_source.get(DETAIL_SOURCE),
            "history": parsed,
            "freshness": self._freshness(
                latest.get("captured_at") if latest else None,
                self.settings.douyin_detail_cache_ttl_hours
                if latest_by_source.get(DETAIL_SOURCE)
                else self.settings.douyin_list_cache_ttl_hours,
            ),
            "coverage": self._snapshot_coverage(latest),
            "warnings": [] if latest else ["该视频尚无指标快照。"],
            "evidence": [
                {"video_id": video_id, "snapshot_id": row["id"], "captured_at": row["captured_at"]}
                for row in parsed[:5]
            ],
        }

    def compare_videos(
        self,
        video_ids: list[str],
        metrics: list[str] | None = None,
        period: str = "30d",
    ) -> dict[str, Any]:
        if len(set(video_ids)) != len(video_ids) or not 2 <= len(video_ids) <= 20:
            raise AppError(VALIDATION_ERROR, "Compare between 2 and 20 unique videos.")
        requested = metrics or [
            "play_count",
            "five_second_completion_rate",
            "completion_rate",
            "like_rate",
            "collect_rate",
            "interaction_rate",
        ]
        allowed_metrics = {
            *DETAIL_METRIC_FIELDS,
            "like_rate", "collect_rate", "comment_rate", "share_rate",
            "play_rate", "interaction_rate",
        }
        unsupported = [metric for metric in requested if metric not in allowed_metrics]
        if unsupported:
            raise AppError(
                VALIDATION_ERROR,
                "Unsupported comparison metrics.",
                False,
                {"unsupported_metrics": unsupported},
            )
        videos = self._videos_by_ids(video_ids)
        snapshots = self._latest_performance_map(video_ids, period)
        rows = []
        warnings: list[str] = []
        for video_id in video_ids:
            snapshot = snapshots.get(video_id)
            values = {}
            for metric in requested:
                values[metric] = snapshot.get(metric) if snapshot else None
            rows.append({"video": self._public_video(videos[video_id]), "metrics": values, "snapshot": snapshot})
            if not snapshot:
                warnings.append(f"作品 {video_id} 在所选周期内没有指标快照。")
        return {
            "status": "completed",
            "period": period,
            "metrics": requested,
            "videos": rows,
            "coverage": self._comparison_coverage(rows, requested),
            "warnings": sorted(set(warnings)),
            "evidence": [
                {"video_id": row["video"]["id"], "snapshot_id": (row["snapshot"] or {}).get("id")}
                for row in rows
            ],
        }

    def get_metric_coverage(
        self,
        period: str = "all",
        video_ids: list[str] | None = None,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        params: list[Any] = [account_id]
        where = "account_id = ? AND source = 'browser_dom'"
        if video_ids:
            self._videos_by_ids(video_ids, account_id)
            placeholders = ",".join("?" for _ in video_ids)
            where += f" AND id IN ({placeholders})"
            params.extend(video_ids)
        videos = self.db.query_all(f"SELECT id FROM videos WHERE {where}", tuple(params))
        ids = [str(video["id"]) for video in videos]
        snapshots_by_video: dict[str, dict[str, dict[str, Any]]] = {
            video_id: {} for video_id in ids
        }
        if ids:
            placeholders = ",".join("?" for _ in ids)
            snapshot_rows = self.db.query_all(
                f"""
                SELECT * FROM video_metric_snapshots
                WHERE video_id IN ({placeholders})
                  {self._period_sql(period, 'captured_at')}
                ORDER BY video_id, source, captured_at DESC, rowid DESC
                """,
                tuple([*ids, *self._period_params(period)]),
            )
            for row in snapshot_rows:
                by_source = snapshots_by_video[str(row["video_id"])]
                by_source.setdefault(str(row["source"]), row)
        else:
            self._period_cutoff(period)

        total = len(ids)
        fields = [
            "play_count", "like_count", "collect_count", "comment_count", "share_count",
            "exposure_count", "five_second_completion_rate", "completion_rate",
            "average_watch_duration_seconds", "follower_gain",
        ]
        basic_fields = {"play_count", "like_count", "collect_count", "comment_count", "share_count"}
        hits = {field: 0 for field in fields}
        missing_reasons: dict[str, int] = {}
        detail_video_count = 0
        list_video_count = 0
        for sources in snapshots_by_video.values():
            detail = sources.get(DETAIL_SOURCE) or {}
            listed = sources.get(LIST_SOURCE) or {}
            detail_video_count += bool(detail)
            list_video_count += bool(listed)
            for field in fields:
                value = detail.get(field)
                if value is None and field in basic_fields:
                    value = listed.get(field)
                if value is not None:
                    hits[field] += 1
                    continue
                reason = None
                for snapshot in (detail, listed if field in basic_fields else {}):
                    try:
                        reasons = json.loads(snapshot.get("missing_reason_json") or "{}")
                    except ValueError:
                        reasons = {}
                    if reasons.get(field):
                        reason = str(reasons[field])
                        break
                reason = reason or "no_snapshot"
                missing_reasons[reason] = missing_reasons.get(reason, 0) + 1
        coverage = {
            "videos": total,
            "source_video_coverage": {
                LIST_SOURCE: list_video_count / total if total else 0.0,
                DETAIL_SOURCE: detail_video_count / total if total else 0.0,
            },
            "field_coverage": {
                field: (hits[field] / total if total else 0.0) for field in fields
            },
            "missing_reasons": missing_reasons,
        }
        warnings = []
        if total == 0:
            warnings.append("本地尚无作品数据。")
        elif any(rate < 0.6 for rate in coverage["field_coverage"].values()):
            warnings.append("部分关键指标覆盖率低于 60%，分析时应降低置信度。")
        return {"status": "completed", "period": period, "coverage": coverage, "warnings": warnings}

    def rank_video_potential(
        self,
        period: str = "30d",
        limit: int = 10,
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise AppError(VALIDATION_ERROR, "limit must be between 1 and 100.")
        listed = self.list_videos(limit=100, offset=0)
        snapshots = self._latest_performance_map(
            [video["id"] for video in listed["videos"]], period
        )
        records = []
        for video in listed["videos"]:
            snapshot = snapshots.get(video["id"])
            if snapshot:
                records.append({"video": video, "snapshot": snapshot})
        metric_weights = weights or {
            "completion_rate": 0.25,
            "five_second_completion_rate": 0.20,
            "collect_rate": 0.15,
            "share_rate": 0.15,
            "like_rate": 0.10,
            "comment_rate": 0.10,
            "play_rate": 0.05,
        }
        allowed_weights = {
            "completion_rate", "five_second_completion_rate", "collect_rate",
            "share_rate", "like_rate", "comment_rate", "play_rate",
        }
        if not metric_weights or any(metric not in allowed_weights for metric in metric_weights):
            raise AppError(VALIDATION_ERROR, "weights contain unsupported metrics.")
        if any(isinstance(weight, bool) for weight in metric_weights.values()):
            raise AppError(VALIDATION_ERROR, "weights must be numeric.")
        try:
            numeric_weights = {metric: float(weight) for metric, weight in metric_weights.items()}
        except (TypeError, ValueError) as exc:
            raise AppError(VALIDATION_ERROR, "weights must be numeric.") from exc
        if (
            any(not math.isfinite(weight) or weight < 0 for weight in numeric_weights.values())
            or sum(numeric_weights.values()) <= 0
        ):
            raise AppError(VALIDATION_ERROR, "weights must be non-negative with a positive total.")
        total_weight = sum(numeric_weights.values())
        metric_weights = {
            metric: weight / total_weight for metric, weight in numeric_weights.items()
        }
        value_sets = {
            metric: [float(row["snapshot"][metric]) for row in records if row["snapshot"].get(metric) is not None]
            for metric in metric_weights
        }
        ranked = []
        for row in records:
            components: dict[str, float] = {}
            available_weight = 0.0
            weighted_score = 0.0
            for metric, weight in metric_weights.items():
                value = row["snapshot"].get(metric)
                if value is None:
                    continue
                score = percentile_rank(float(value), value_sets[metric])
                components[metric] = round(score, 2)
                available_weight += float(weight)
                weighted_score += score * float(weight)
            total_score = round(weighted_score / available_weight, 2) if available_weight >= 0.6 else None
            ranked.append(
                {
                    "video": row["video"],
                    "potential_score": total_score,
                    "available_weight": round(available_weight, 3),
                    "components": components,
                    "small_sample": len(records) < 10,
                    "snapshot_id": row["snapshot"]["id"],
                }
            )
        ranked.sort(key=lambda item: item["potential_score"] if item["potential_score"] is not None else -1, reverse=True)
        return {
            "status": "completed",
            "period": period,
            "videos": ranked[:limit],
            "warnings": ["样本少于 10 条，分位数仅供参考。"] if len(records) < 10 else [],
            "score_version": "potential-v1",
        }

    def generate_review(
        self,
        period: str = "30d",
        focus: str = "potential",
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        if focus != "potential":
            raise AppError(VALIDATION_ERROR, "V1 focus currently supports only potential.")
        coverage = self.get_metric_coverage(period=period)
        ranking = self.rank_video_potential(period=period, limit=min(recent_limit, 20))
        listed = self.list_videos(limit=min(recent_limit, 100), offset=0)
        warnings = sorted(set([*coverage["warnings"], *ranking["warnings"]]))
        findings = []
        for item in ranking["videos"][:5]:
            findings.append(
                {
                    "type": "potential_candidate",
                    "video_id": item["video"]["id"],
                    "title": item["video"].get("title"),
                    "score": item["potential_score"],
                    "evidence": item["components"],
                }
            )
        return {
            "status": "completed",
            "period": period,
            "focus": focus,
            "data_range": {"videos": listed["total"], "selected": len(listed["videos"])},
            "coverage": coverage["coverage"],
            "findings": findings,
            "ranked_videos": ranking["videos"],
            "warnings": warnings,
            "confidence": "low" if warnings else "medium",
            "evidence": [
                {"video_id": item["video"]["id"], "snapshot_id": item["snapshot_id"]}
                for item in ranking["videos"][:5]
            ],
        }

    def export_data(
        self,
        format: str = "json",
        period: str = "all",
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        normalized = format.lower()
        if normalized not in {"json", "csv"}:
            raise AppError(VALIDATION_ERROR, "format must be json or csv.")
        rows = self.db.query_all(
            f"""
            SELECT v.id AS video_id, v.title, v.publish_time, v.duration,
                   s.source, s.captured_at, s.exposure_count, s.play_count,
                   s.five_second_completion_rate, s.completion_rate,
                   s.average_watch_duration_seconds, s.like_count, s.collect_count,
                   s.comment_count, s.share_count, s.follower_gain,
                   d.like_rate, d.collect_rate, d.comment_rate, d.share_rate,
                   d.play_rate, d.interaction_rate, d.formula_version
            FROM videos AS v
            LEFT JOIN video_metric_snapshots AS s ON s.video_id = v.id
            LEFT JOIN video_derived_metrics AS d ON d.snapshot_id = s.id
            WHERE v.account_id = ? AND v.source = 'browser_dom'
              {self._period_sql(period, 's.captured_at')}
            ORDER BY v.publish_time DESC, s.captured_at DESC
            """,
            tuple([BROWSER_DEFAULT_ACCOUNT_ID, *self._period_params(period)]),
        )
        exports_dir = self.settings.data_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        target = Path(output_path) if output_path else exports_dir / f"douyin-{period}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.{normalized}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if normalized == "json":
            target.write_text(json.dumps({"period": period, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            with target.open("w", encoding="utf-8-sig", newline="") as handle:
                fieldnames = list(rows[0].keys()) if rows else ["video_id", "title"]
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        return {"status": "completed", "format": normalized, "period": period, "path": str(target), "row_count": len(rows)}

    def purge_local_data(self) -> dict[str, Any]:
        """Delete the local cache and dedicated profile after the CLI confirms intent."""

        self.close_browser()
        data_dir = self.settings.data_dir.resolve()
        profile_dir = self.settings.douyin_browser_profile_dir.resolve()
        if data_dir == Path(data_dir.anchor) or profile_dir == Path(profile_dir.anchor):
            raise AppError(VALIDATION_ERROR, "Refusing to purge a filesystem root.")
        if profile_dir in data_dir.parents:
            raise AppError(
                VALIDATION_ERROR,
                "Browser profile cannot be an ancestor of DATA_DIR during purge.",
            )

        removed: list[str] = []
        lock = self._profile_lock()
        with lock:
            if data_dir.exists():
                for child in data_dir.iterdir():
                    if child.resolve() == lock.path.resolve():
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
                    removed.append(str(child))
            if (
                profile_dir.exists()
                and profile_dir != data_dir
                and data_dir not in profile_dir.parents
            ):
                shutil.rmtree(profile_dir)
                removed.append(str(profile_dir))
        return {
            "status": "completed",
            "removed": removed,
            "data_dir": str(data_dir),
            "profile_dir": str(profile_dir),
        }

    # Legacy snapshot report -----------------------------------------

    def refresh_report(
        self,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
        period: str = "latest",
    ) -> dict[str, Any]:
        snapshot = self._latest_snapshot(account_id)
        structured_videos = snapshot["extracted"].get("structured_videos", [])
        metric_totals = {
            key: sum(int(video.get(key) or 0) for video in structured_videos if isinstance(video, dict))
            for key in ("play_count", "like_count", "comment_count", "share_count")
        }
        load_stats = snapshot["extracted"].get("load_stats", {})
        summary = {
            "account_id": account_id,
            "period": period,
            "snapshot_id": snapshot["id"],
            "snapshot_created_at": snapshot["created_at"],
            "source_url": snapshot["source_url"],
            "title": snapshot.get("title"),
            "login_status": snapshot["status"],
            "video_candidate_count": len(snapshot["extracted"].get("video_candidates", [])),
            "structured_video_count": len(structured_videos),
            "page_total_video_count": load_stats.get("page_total_video_count"),
            "loaded_video_count": load_stats.get("loaded_card_count"),
            "metric_totals": metric_totals,
            "data_source": "browser_snapshot",
            "analysis_notes": ["仅汇总创作者中心页面真实展示并已采集的数据。"],
        }
        report_path = self._write_report(account_id, period, summary, snapshot)
        report_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO reports (id, account_id, period, report_path, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, account_id, period, str(report_path), json.dumps(summary, ensure_ascii=False), utc_now_iso()),
        )
        return {"report_id": report_id, "report_path": str(report_path), "summary": summary}

    def close_browser(self) -> None:
        if self._browser_executor is not None:
            self._browser_executor.close_session()
            return
        session = self._session
        self._session = None
        try:
            if session is not None:
                session.close()
        finally:
            lock = self._active_profile_lock
            self._active_profile_lock = None
            if lock is not None:
                lock.release()

    def latest_snapshot_summary(self, account_id: str = BROWSER_DEFAULT_ACCOUNT_ID) -> dict[str, Any]:
        snapshot = self._latest_snapshot(account_id)
        extracted = snapshot["extracted"]
        source_url = urlsplit(snapshot["source_url"])
        safe_source_url = urlunsplit((source_url.scheme, source_url.netloc, source_url.path, "", ""))
        return {
            "snapshot_id": snapshot["id"],
            "account_id": snapshot["account_id"],
            "source_url": safe_source_url,
            "title": snapshot.get("title"),
            "login_status": snapshot["status"],
            "created_at": snapshot["created_at"],
            "text_line_count": len(extracted.get("text_lines", [])),
            "video_candidate_count": len(extracted.get("video_candidates", [])),
            "structured_video_count": len(extracted.get("structured_videos", [])),
            "page_total_video_count": extracted.get("load_stats", {}).get("page_total_video_count"),
            "loaded_video_count": extracted.get("load_stats", {}).get("loaded_card_count"),
        }

    # Persistence helpers --------------------------------------------

    def _get_session(self, headless: bool | None = None) -> BrowserSession:
        if self._session is None:
            self._session = self._session_factory() if self._session_factory else BrowserSession(self.settings, headless=headless)
        return self._session

    def _profile_lock(self) -> ProfileLock:
        return ProfileLock(
            self.settings.douyin_browser_profile_dir,
            self.settings.douyin_profile_lock_filename,
        )

    @contextmanager
    def _browser_access(self):
        if self._active_profile_lock is not None:
            yield
            return
        lock = self._profile_lock()
        lock.acquire()
        self._active_profile_lock = lock
        try:
            yield
        finally:
            session_running = self._session is not None and self._session.is_running
            if not session_running and self._active_profile_lock is lock:
                self._active_profile_lock = None
                lock.release()

    def _start_job(self, account_id: str, job_type: str, parser_version: str | None = None) -> str:
        job_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO sync_jobs (id, account_id, job_type, status, started_at, parser_version) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, account_id, job_type, "running", utc_now_iso(), parser_version),
        )
        return job_id

    def _finish_job(
        self,
        job_id: str,
        status: str,
        error_type: str | None = None,
        error_message: str | None = None,
        progress: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
        resume_cursor: int | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_type = ?, error_message = ?,
              progress_json = ?, coverage_json = ?, resume_cursor = ? WHERE id = ?
            """,
            (
                status,
                utc_now_iso(),
                error_type,
                error_message,
                json.dumps(progress, ensure_ascii=False) if progress is not None else None,
                json.dumps(coverage, ensure_ascii=False) if coverage is not None else None,
                resume_cursor,
                job_id,
            ),
        )

    def _save_snapshot(self, account_id: str, snapshot: dict[str, Any]) -> str:
        snapshot_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO browser_snapshots (id, account_id, source_url, title, status, extracted_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                account_id,
                snapshot["source_url"],
                snapshot["title"],
                snapshot["login_status"],
                json.dumps(snapshot, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        return snapshot_id

    def _verify_browser_account_identity(
        self,
        account_id: str,
        videos: list[dict[str, Any]],
        verified_at: str,
    ) -> dict[str, Any]:
        """Bind or verify one local account using salted, irreversible content anchors."""
        current_sources = self._identity_anchor_sources(videos)
        binding = self.db.query_one(
            "SELECT * FROM browser_account_bindings WHERE account_id = ?",
            (account_id,),
        )
        if not current_sources:
            if binding:
                raise AppError(
                    ACCOUNT_IDENTITY_UNRESOLVED,
                    "当前页面没有足够的作品锚点，已拒绝确认账号并写入数据。",
                    retryable=True,
                    extra={"next_action": "重新加载作品列表；如确认更换账号，请执行 purge --yes 后重新登录。"},
                )
            return {"status": "unbound_empty_account", "bound": False, "anchor_count": 0}

        if binding is None:
            existing_rows = self.db.query_all(
                "SELECT item_id, video_id, title, publish_time, duration, source_fingerprint "
                "FROM videos WHERE account_id = ? AND source = 'browser_dom'",
                (account_id,),
            )
            existing_sources = self._identity_anchor_sources(existing_rows)
            salt = uuid.uuid4().hex
            if existing_sources:
                existing_hashes = self._identity_anchor_hashes(salt, existing_sources)
                current_hashes = self._identity_anchor_hashes(salt, current_sources)
                if not existing_hashes.intersection(current_hashes):
                    raise AppError(
                        ACCOUNT_MISMATCH,
                        "当前登录账号与本地已有数据不一致，已拒绝写入。",
                        retryable=False,
                        extra={"next_action": "确认是否误切账号；如需更换账号，请先执行 purge --yes。"},
                    )
            selected = self._select_identity_anchors(current_sources)
            hashes = sorted(self._identity_anchor_hashes(salt, selected))
            self.db.execute(
                "INSERT INTO browser_account_bindings "
                "(account_id, fingerprint_salt, anchor_hashes_json, anchor_count, created_at, last_verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    salt,
                    json.dumps(hashes),
                    len(hashes),
                    verified_at,
                    verified_at,
                ),
            )
            return {"status": "bound", "bound": True, "anchor_count": len(hashes)}

        salt = str(binding.get("fingerprint_salt") or "")
        try:
            stored_hashes = {
                str(item)
                for item in json.loads(binding.get("anchor_hashes_json") or "[]")
                if item
            }
        except (TypeError, ValueError):
            stored_hashes = set()
        if not salt or not stored_hashes:
            raise AppError(
                ACCOUNT_IDENTITY_UNRESOLVED,
                "本地账号指纹损坏，已拒绝写入数据。",
                retryable=False,
                extra={"next_action": "请备份所需数据后执行 purge --yes，再重新登录绑定。"},
            )
        current_hashes = self._identity_anchor_hashes(salt, current_sources)
        overlap_count = len(stored_hashes.intersection(current_hashes))
        if overlap_count == 0:
            raise AppError(
                ACCOUNT_MISMATCH,
                "当前登录账号与本地绑定账号不一致，已拒绝写入。",
                retryable=False,
                extra={"next_action": "请切回原账号；如需更换账号，请先执行 purge --yes。"},
            )

        selected = self._select_identity_anchors(current_sources)
        refreshed_hashes = sorted(self._identity_anchor_hashes(salt, selected))
        self.db.execute(
            "UPDATE browser_account_bindings SET anchor_hashes_json = ?, anchor_count = ?, "
            "last_verified_at = ? WHERE account_id = ?",
            (json.dumps(refreshed_hashes), len(refreshed_hashes), verified_at, account_id),
        )
        return {
            "status": "verified",
            "bound": True,
            "anchor_count": len(refreshed_hashes),
            "overlap_count": overlap_count,
        }

    @staticmethod
    def _identity_anchor_sources(videos: list[dict[str, Any]]) -> list[str]:
        sources: list[tuple[int, str]] = []
        for video in videos:
            title = str(video.get("title") or "").strip()
            publish_time = str(video.get("publish_time") or "").strip()
            if not title or not publish_time:
                continue
            # Keep the anchor compatible with databases created before duration
            # was collected. The raw title and time are hashed immediately.
            raw = "|".join((publish_time, title))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            try:
                sort_time = int(float(publish_time))
            except ValueError:
                sort_time = 0
            sources.append((sort_time, digest))
        return [value for _, value in sorted(set(sources))]

    @staticmethod
    def _select_identity_anchors(sources: list[str], limit: int = 24) -> list[str]:
        if len(sources) <= limit:
            return list(sources)
        last_index = len(sources) - 1
        indices = {round(index * last_index / (limit - 1)) for index in range(limit)}
        return [sources[index] for index in sorted(indices)]

    @staticmethod
    def _identity_anchor_hashes(salt: str, sources: list[str]) -> set[str]:
        return {
            hashlib.sha256(f"{salt}|{source}".encode("utf-8")).hexdigest()
            for source in sources
        }

    @staticmethod
    def _public_account_binding(binding: dict[str, Any] | None) -> dict[str, Any]:
        if not binding:
            return {"bound": False, "anchor_count": 0, "last_verified_at": None}
        return {
            "bound": True,
            "anchor_count": int(binding.get("anchor_count") or 0),
            "created_at": binding.get("created_at"),
            "last_verified_at": binding.get("last_verified_at"),
        }

    @staticmethod
    def _public_profile_lock(lock: dict[str, Any]) -> dict[str, Any]:
        return {
            "locked": bool(lock.get("locked")),
            "acquired_at": lock.get("acquired_at") if lock.get("locked") else None,
        }

    def _upsert_structured_videos(
        self,
        account_id: str,
        videos: list[dict[str, Any]],
        job_id: str,
        captured_at: str,
    ) -> tuple[int, int]:
        metric_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        snapshot_count = 0
        with self.db.transaction() as conn:
            for video in videos:
                platform_id = video.get("platform_item_id")
                existing = None
                if platform_id:
                    existing = conn.execute(
                        "SELECT id FROM videos WHERE account_id = ? "
                        "AND (item_id = ? OR video_id = ?) LIMIT 1",
                        (account_id, platform_id, platform_id),
                    ).fetchone()
                if existing is None:
                    existing = conn.execute(
                        "SELECT id FROM videos WHERE account_id = ? AND publish_time = ? "
                        "AND title = ? AND source = 'browser_dom' LIMIT 1",
                        (account_id, video["publish_time"], video["title"]),
                    ).fetchone()
                local_id = str(existing[0]) if existing else self._local_video_id(account_id, video)
                conn.execute(
                    """
                    INSERT INTO videos
                      (id, account_id, item_id, video_id, title, publish_time, cover_url,
                       video_url, duration, status, source_fingerprint, parser_version,
                       first_seen_at, last_seen_at, is_active, source, created_at, updated_at,
                       visibility, content_kind, classification_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      item_id = COALESCE(excluded.item_id, videos.item_id),
                      video_id = COALESCE(excluded.video_id, videos.video_id),
                      title = excluded.title, publish_time = excluded.publish_time,
                      cover_url = excluded.cover_url,
                      video_url = COALESCE(excluded.video_url, videos.video_url),
                      duration = excluded.duration, status = excluded.status,
                      source_fingerprint = excluded.source_fingerprint,
                      parser_version = excluded.parser_version,
                      last_seen_at = excluded.last_seen_at, is_active = 1,
                      source = excluded.source, updated_at = excluded.updated_at,
                      visibility = excluded.visibility,
                      content_kind = excluded.content_kind,
                      classification_source = excluded.classification_source
                    """,
                    (
                        local_id,
                        account_id,
                        video.get("platform_item_id"),
                        video.get("platform_item_id"),
                        video["title"],
                        video["publish_time"],
                        video.get("cover_url"),
                        video.get("video_url"),
                        video.get("duration"),
                        video.get("status"),
                        video.get("source_fingerprint"),
                        self.settings.douyin_list_parser_version,
                        captured_at,
                        captured_at,
                        "browser_dom",
                        captured_at,
                        captured_at,
                        video.get("visibility", "unknown"),
                        video.get("content_kind", "unknown"),
                        video.get("classification_source"),
                    ),
                )
                metric_id = self._local_metric_id(local_id, metric_date)
                conn.execute(
                    """
                    INSERT INTO video_metrics
                      (id, video_id, account_id, metric_date, play_count, like_count,
                       comment_count, share_count, collect_count, source, capability_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      play_count = excluded.play_count, like_count = excluded.like_count,
                      comment_count = excluded.comment_count, share_count = excluded.share_count,
                      collect_count = excluded.collect_count, created_at = excluded.created_at
                    """,
                    (
                        metric_id,
                        local_id,
                        account_id,
                        metric_date,
                        video.get("play_count"),
                        video.get("like_count"),
                        video.get("comment_count"),
                        video.get("share_count"),
                        video.get("collect_count"),
                        "browser_dom",
                        captured_at,
                    ),
                )
                raw = {field: video.get(field) for field in ("play_count", "like_count", "collect_count", "comment_count", "share_count")}
                missing = {field: "not_displayed" for field, value in raw.items() if value is None}
                snapshot_id = self._insert_metric_snapshot_conn(
                    conn,
                    job_id,
                    local_id,
                    account_id,
                    LIST_SOURCE,
                    raw,
                    raw,
                    missing,
                    "complete" if not missing else "partial",
                    self.settings.douyin_list_parser_version,
                    captured_at,
                )
                self._insert_derived_conn(conn, snapshot_id, raw, captured_at)
                snapshot_count += 1
        return len(videos), snapshot_count

    def _save_metric_snapshot(
        self,
        job_id: str,
        video_id: str,
        account_id: str,
        source: str,
        metrics: dict[str, Any],
        raw_metrics: dict[str, Any],
        missing_reasons: dict[str, Any],
        quality: str,
        parser_version: str,
        captured_at: str,
    ) -> str:
        with self.db.transaction() as conn:
            return self._insert_metric_snapshot_conn(
                conn, job_id, video_id, account_id, source, metrics, raw_metrics,
                missing_reasons, quality, parser_version, captured_at
            )

    def _bind_video_detail_identity(
        self,
        video: dict[str, Any],
        detail_url: str,
        require_platform_id: bool = False,
    ) -> None:
        platform_id = detail_video_id_from_url(detail_url)
        existing_id = str(video.get("item_id") or video.get("video_id") or "").strip()
        if existing_id and platform_id and existing_id != platform_id:
            raise AppError(
                VIDEO_IDENTITY_UNRESOLVED,
                "详情页作品 ID 与本地记录冲突，已拒绝绑定和采集。",
                retryable=False,
            )
        if require_platform_id and not platform_id:
            raise AppError(
                VIDEO_IDENTITY_UNRESOLVED,
                "详情页 URL 中未找到稳定作品 ID，已拒绝绑定和采集。",
                retryable=False,
            )
        resolved_id = platform_id or existing_id or None
        sanitized_url = self._sanitize_source_url(detail_url)
        if not resolved_id and not sanitized_url:
            return
        if resolved_id:
            conflict = self.db.query_one(
                "SELECT id FROM videos WHERE account_id = ? AND id <> ? "
                "AND (item_id = ? OR video_id = ?) LIMIT 1",
                (
                    video["account_id"],
                    video["id"],
                    resolved_id,
                    resolved_id,
                ),
            )
            if conflict:
                raise AppError(
                    VIDEO_IDENTITY_UNRESOLVED,
                    "该详情页作品 ID 已绑定其他本地记录，已拒绝覆盖。",
                    retryable=False,
                )
        updated_at = utc_now_iso()
        self.db.execute(
            "UPDATE videos SET item_id = COALESCE(?, item_id), "
            "video_id = COALESCE(?, video_id), video_url = COALESCE(?, video_url), "
            "updated_at = ? WHERE id = ? AND account_id = ?",
            (
                resolved_id,
                resolved_id,
                sanitized_url,
                updated_at,
                video["id"],
                video["account_id"],
            ),
        )
        if resolved_id:
            video["item_id"] = resolved_id
            video["video_id"] = resolved_id
            video["platform_item_id"] = resolved_id
        if sanitized_url:
            video["video_url"] = sanitized_url

    @staticmethod
    def _sanitize_source_url(value: str) -> str | None:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    @staticmethod
    def _insert_metric_snapshot_conn(
        conn: Any,
        job_id: str,
        video_id: str,
        account_id: str,
        source: str,
        metrics: dict[str, Any],
        raw_metrics: dict[str, Any],
        missing_reasons: dict[str, Any],
        quality: str,
        parser_version: str,
        captured_at: str,
    ) -> str:
        snapshot_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO video_metric_snapshots
              (id, sync_job_id, video_id, account_id, source, captured_at,
               exposure_count, play_count, five_second_completion_rate, completion_rate,
               average_watch_duration_seconds, like_count, collect_count, comment_count,
               share_count, follower_gain, raw_metric_json, missing_reason_json,
               quality, parser_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, job_id, video_id, account_id, source, captured_at,
                metrics.get("exposure_count"), metrics.get("play_count"),
                metrics.get("five_second_completion_rate"), metrics.get("completion_rate"),
                metrics.get("average_watch_duration_seconds"), metrics.get("like_count"),
                metrics.get("collect_count"), metrics.get("comment_count"),
                metrics.get("share_count"), metrics.get("follower_gain"),
                json.dumps(raw_metrics, ensure_ascii=False),
                json.dumps(missing_reasons, ensure_ascii=False),
                quality, parser_version, captured_at,
            ),
        )
        return snapshot_id

    def _save_derived_metrics(self, snapshot_id: str, metrics: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            self._insert_derived_conn(conn, snapshot_id, metrics, utc_now_iso())

    @staticmethod
    def _insert_derived_conn(conn: Any, snapshot_id: str, metrics: dict[str, Any], at: str) -> None:
        derived = compute_derived_metrics(metrics)
        conn.execute(
            """
            INSERT OR REPLACE INTO video_derived_metrics
              (id, snapshot_id, like_rate, collect_rate, comment_rate, share_rate,
               play_rate, interaction_rate, formula_version, calculated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hashlib.sha256(f"{snapshot_id}|{FORMULA_VERSION}".encode()).hexdigest(),
                snapshot_id,
                derived["like_rate"], derived["collect_rate"], derived["comment_rate"],
                derived["share_rate"], derived["play_rate"], derived["interaction_rate"],
                derived["formula_version"], at,
            ),
        )

    # Small helpers ---------------------------------------------------

    @staticmethod
    def _local_video_id(account_id: str, video: dict[str, Any]) -> str:
        stable = video.get("platform_item_id")
        fallback = str(video["publish_time"]) + "|" + video["title"]
        identity = f"browser_dom|{account_id}|{stable or fallback}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _local_metric_id(video_id: str, metric_date: str) -> str:
        return hashlib.sha256(f"browser_dom|{video_id}|{metric_date}".encode("utf-8")).hexdigest()

    def _latest_snapshot(self, account_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM browser_snapshots WHERE account_id = ? ORDER BY created_at DESC LIMIT 1",
            (account_id,),
        )
        if not row:
            raise AppError(DATA_NOT_AVAILABLE, "No browser snapshot is available. Sync creator data first.", True)
        row["extracted"] = json.loads(row.get("extracted_json") or "{}")
        return row

    def _latest_metric_snapshot(
        self, video_id: str | None = None, source: str | None = None
    ) -> dict[str, Any] | None:
        where = ["account_id = ?"]
        params: list[Any] = [BROWSER_DEFAULT_ACCOUNT_ID]
        if video_id:
            where.append("video_id = ?")
            params.append(video_id)
        if source:
            where.append("source = ?")
            params.append(source)
        return self.db.query_one(
            f"SELECT * FROM video_metric_snapshots WHERE {' AND '.join(where)} "
            "ORDER BY captured_at DESC, rowid DESC LIMIT 1",
            tuple(params),
        )

    def _has_fresh_detail(self, video_id: str) -> bool:
        latest = self._latest_metric_snapshot(video_id=video_id, source=DETAIL_SOURCE)
        if not latest or latest.get("parser_version") != self.settings.douyin_detail_parser_version:
            return False
        return not self._freshness(
            latest.get("captured_at"),
            self.settings.douyin_detail_cache_ttl_hours,
        )["is_stale"]

    def _select_detail_videos(
        self, account_id: str, video_ids: list[str] | None, recent_limit: int
    ) -> list[dict[str, Any]]:
        if video_ids:
            placeholders = ",".join("?" for _ in video_ids)
            rows = self.db.query_all(
                f"SELECT * FROM videos WHERE account_id = ? AND id IN ({placeholders}) ORDER BY publish_time DESC",
                tuple([account_id, *video_ids]),
            )
            found = {row["id"] for row in rows}
            missing = [video_id for video_id in video_ids if video_id not in found]
            if missing:
                raise AppError(DATA_NOT_AVAILABLE, "Some requested videos are not in the local cache.", False, {"missing_video_ids": missing})
            return rows
        return self.db.query_all(
            "SELECT * FROM videos WHERE account_id = ? AND source = 'browser_dom' "
            "ORDER BY publish_time DESC, id ASC LIMIT ?",
            (account_id, recent_limit),
        )

    def _videos_by_ids(
        self,
        video_ids: list[str],
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
    ) -> dict[str, dict[str, Any]]:
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.query_all(
            f"SELECT * FROM videos WHERE account_id = ? AND id IN ({placeholders})",
            tuple([account_id, *video_ids]),
        )
        result = {str(row["id"]): row for row in rows}
        missing = [video_id for video_id in video_ids if video_id not in result]
        if missing:
            raise AppError(
                DATA_NOT_AVAILABLE,
                "Some requested videos are not in the local cache.",
                False,
                {"missing_video_ids": missing},
            )
        return result

    def _latest_performance_map(
        self, video_ids: list[str], period: str
    ) -> dict[str, dict[str, Any]]:
        if not video_ids:
            self._period_cutoff(period)
            return {}
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.query_all(
            f"""
            SELECT s.*, d.like_rate, d.collect_rate, d.comment_rate, d.share_rate,
                   d.play_rate, d.interaction_rate, d.formula_version
            FROM video_metric_snapshots AS s
            LEFT JOIN video_derived_metrics AS d ON d.snapshot_id = s.id
            WHERE s.video_id IN ({placeholders})
              {self._period_sql(period, 's.captured_at')}
            ORDER BY s.video_id,
              CASE s.source WHEN '{DETAIL_SOURCE}' THEN 0 ELSE 1 END,
              s.captured_at DESC, s.rowid DESC
            """,
            tuple([*video_ids, *self._period_params(period)]),
        )
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            video_id = str(row["video_id"])
            if video_id not in latest:
                latest[video_id] = self._snapshot_row(row)
        return latest

    @staticmethod
    def _period_cutoff(period: str) -> str | None:
        normalized = period.strip().lower()
        if normalized in {"all", "latest"}:
            return None
        match = re.fullmatch(r"(\d{1,4})d", normalized)
        if not match or int(match.group(1)) < 1:
            raise AppError(
                VALIDATION_ERROR,
                "period must be all, latest, or a day window such as 7d/30d/90d.",
            )
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(match.group(1)))
        return cutoff.replace(microsecond=0).isoformat()

    @classmethod
    def _period_sql(cls, period: str, column: str) -> str:
        return f"AND {column} >= ?" if cls._period_cutoff(period) else ""

    @classmethod
    def _period_params(cls, period: str) -> list[str]:
        cutoff = cls._period_cutoff(period)
        return [cutoff] if cutoff else []

    @staticmethod
    def _freshness(captured_at: str | None, ttl_hours: int) -> dict[str, Any]:
        if not captured_at:
            return {"captured_at": None, "age_hours": None, "is_stale": True, "ttl_hours": ttl_hours}
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
        return {"captured_at": captured_at, "age_hours": round(age, 3), "is_stale": age > ttl_hours, "ttl_hours": ttl_hours}

    @staticmethod
    def _validate_mode(mode: str) -> None:
        if mode not in {"visible", "background_first"}:
            raise AppError(
                VALIDATION_ERROR, "mode must be visible or background_first."
            )

    @staticmethod
    def _list_coverage(videos: list[dict[str, Any]]) -> dict[str, Any]:
        fields = ("play_count", "like_count", "collect_count", "comment_count", "share_count")
        total = len(videos)
        return {
            "videos": total,
            "field_coverage": {
                field: (sum(video.get(field) is not None for video in videos) / total if total else 0.0)
                for field in fields
            },
        }

    @staticmethod
    def _field_coverage(hits: dict[str, int], total: int) -> dict[str, Any]:
        return {"videos": total, "field_coverage": {field: count / total if total else 0.0 for field, count in hits.items()}}

    @staticmethod
    def _snapshot_coverage(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not snapshot:
            return {"valid_fields": 0, "total_fields": len(DETAIL_METRIC_FIELDS), "rate": 0.0}
        valid = sum(snapshot.get(field) is not None for field in DETAIL_METRIC_FIELDS)
        return {"valid_fields": valid, "total_fields": len(DETAIL_METRIC_FIELDS), "rate": valid / len(DETAIL_METRIC_FIELDS)}

    @staticmethod
    def _comparison_coverage(rows: list[dict[str, Any]], metrics: list[str]) -> dict[str, Any]:
        total = len(rows)
        return {
            "videos": total,
            "field_coverage": {
                metric: (sum(row["metrics"].get(metric) is not None for row in rows) / total if total else 0.0)
                for metric in metrics
            },
        }

    @staticmethod
    def _safe_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if not job:
            return None
        result = {key: value for key, value in job.items() if key not in {"error_message"}}
        for key in ("progress_json", "coverage_json"):
            if result.get(key):
                try:
                    result[key.removesuffix("_json")] = json.loads(result.pop(key))
                except ValueError:
                    result.pop(key, None)
        return result

    @staticmethod
    def _public_video(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"], "title": row.get("title"), "publish_time": row.get("publish_time"),
            "duration": row.get("duration"), "status": row.get("status"), "cover_url": row.get("cover_url"),
        }

    def _video_row_to_result(self, row: dict[str, Any]) -> dict[str, Any]:
        derived = {field: row.get(field) for field in ("like_rate", "collect_rate", "comment_rate", "share_rate", "play_rate", "interaction_rate")}
        return {
            "id": row["id"], "account_id": row["account_id"], "title": row.get("title"),
            "publish_time": row.get("publish_time"), "cover_url": row.get("cover_url"),
            "duration": row.get("duration"), "status": row.get("status"), "source": row["source"],
            "updated_at": row["updated_at"],
            "latest_metrics": {
                "metric_date": str(row.get("captured_at") or "")[:10] or None,
                "captured_at": row.get("captured_at"), "play_count": row.get("play_count"),
                "like_count": row.get("like_count"), "comment_count": row.get("comment_count"),
                "share_count": row.get("share_count"), "collect_count": row.get("collect_count"),
                **derived, "formula_version": row.get("formula_version"),
            },
        }

    @staticmethod
    def _snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source_key, target_key in (("raw_metric_json", "raw_metrics"), ("missing_reason_json", "missing_reasons")):
            try:
                result[target_key] = json.loads(result.pop(source_key) or "{}")
            except ValueError:
                result[target_key] = {}
        return result

    def _detail_sync_result(
        self,
        job_id: str,
        status: str,
        total: int,
        cursor: int,
        next_cursor: int | None,
        succeeded: int,
        partial: int,
        cached: int,
        failures: list[dict[str, Any]],
        field_hits: dict[str, int],
        captured_at: str | None,
        login_status: str,
    ) -> dict[str, Any]:
        denominator = max(1, succeeded + partial + cached)
        return {
            "sync_job_id": job_id, "status": status, "login_status": login_status,
            "target_video_count": total, "batch_cursor": cursor, "next_cursor": next_cursor,
            "succeeded": succeeded, "partial": partial, "cache_hits": cached,
            "failed": len(failures), "failures": failures,
            "captured_at": captured_at, "parser_version": self.settings.douyin_detail_parser_version,
            "coverage": self._field_coverage(field_hits, denominator),
            "freshness": self._freshness(captured_at, self.settings.douyin_detail_cache_ttl_hours),
            "warnings": ["部分视频或指标未能采集，请查看 failures 和覆盖率。"] if failures or partial else [],
            "next_action": self._login_next_action(login_status) or (
                {"type": "continue", "cursor": next_cursor} if next_cursor is not None else None
            ),
        }

    @staticmethod
    def _job_status_from_login_status(login_status: str) -> str:
        return "user_action_required" if login_status in {LOGIN_REQUIRED, VERIFICATION_REQUIRED} else "completed"

    @staticmethod
    def _login_next_action(login_status: str) -> dict[str, Any] | None:
        if login_status == LOGIN_REQUIRED:
            return {"type": "scan_login", "message": "请在已打开的 Chrome 中扫码登录，然后重试同步。"}
        if login_status == VERIFICATION_REQUIRED:
            return {"type": "complete_verification", "message": "请在已打开的 Chrome 中完成安全验证，然后重试同步。"}
        return None

    @staticmethod
    def _sync_notes(login_status: str, job_status: str = "completed") -> list[str]:
        if login_status == LOGIN_REQUIRED:
            return ["登录已失效，请在可见 Chrome 中重新扫码。"]
        if login_status == VERIFICATION_REQUIRED:
            return ["抖音要求安全验证，请在可见 Chrome 中完成后重试。"]
        if job_status == "partial":
            return ["作品列表未达到页面声明数量，本次结果按部分成功保存。"]
        return []

    @staticmethod
    def _next_action_from_results(results: dict[str, Any]) -> Any:
        for result in results.values():
            if result.get("next_action"):
                return result["next_action"]
        return None

    def _write_report(
        self,
        account_id: str,
        period: str,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> Path:
        reports_dir = self.settings.data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{account_id}_browser_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
        report_path = reports_dir / filename
        report_path.write_text(self._render_markdown(summary, snapshot), encoding="utf-8")
        return report_path

    @staticmethod
    def _render_markdown(summary: dict[str, Any], snapshot: dict[str, Any]) -> str:
        candidates = snapshot["extracted"].get("video_candidates", [])
        lines = [
            "# 抖音浏览器快照复盘", "", "## 数据来源", "",
            "- 浏览器登录态页面快照", "- 仅包含页面真实展示的数据，不包含登录凭证", "",
            "## 快照概览", "", f"- 账号 ID：{summary['account_id']}",
            f"- 页面标题：{summary.get('title') or '未知'}", f"- 页面 URL：{summary['source_url']}",
            f"- 登录状态：{summary['login_status']}", f"- 页面作品总数：{summary.get('page_total_video_count')}",
            f"- 已加载作品数量：{summary.get('loaded_video_count')}", f"- 结构化作品数量：{summary.get('structured_video_count')}",
            "", "## 当前列表指标汇总", "",
            f"- 播放：{summary.get('metric_totals', {}).get('play_count', 0)}",
            f"- 点赞：{summary.get('metric_totals', {}).get('like_count', 0)}",
            f"- 评论：{summary.get('metric_totals', {}).get('comment_count', 0)}",
            f"- 分享：{summary.get('metric_totals', {}).get('share_count', 0)}", "",
            "## 疑似视频文本", "",
        ]
        lines.extend(f"- {item.get('text', '')}" for item in candidates[:20])
        if not candidates:
            lines.append("- 当前快照未提取到疑似视频文本")
        lines.extend(["", "## 说明", "", "数据缺失不代表表现为零；请结合采集时间和覆盖率使用。", ""])
        return "\n".join(lines)
