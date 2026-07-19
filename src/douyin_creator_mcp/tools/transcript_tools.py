"""Fast MCP tools for persistent transcript runs and immutable content."""

from __future__ import annotations

from typing import Any

from fastmcp import Context

from ..errors import (
    DATA_NOT_AVAILABLE,
    INVALID_CURSOR,
    TRANSCRIPT_DISABLED,
    VALIDATION_ERROR,
    AppError,
)
from ..responses import response_from_exception, success_response
from ..services.browser_service import BROWSER_DEFAULT_ACCOUNT_ID


def _decode_run_cursor(
    signer: Any,
    cursor: str | None,
    *,
    kind: str,
    expected_id: str,
    expected_account_id: str | None = None,
) -> dict[str, Any] | None:
    if not cursor:
        return None
    payload = signer.decode(cursor)
    identity_key = "run_id" if kind == "run_items" else "account_id"
    if (
        payload.get("kind") != kind
        or payload.get(identity_key) != expected_id
        or (
            expected_account_id is not None
            and payload.get("account_id") != expected_account_id
        )
    ):
        raise AppError(INVALID_CURSOR, "Cursor does not match this run query.")
    return payload


def register_transcript_tools(mcp: Any, services: Any | None = None) -> None:
    def resolve(ctx: Context) -> Any:
        if services is not None:
            return services
        container = ctx.lifespan_context.get("services")
        if container is None:
            raise RuntimeError("Runtime lifespan is not active.")
        return container

    def call(ctx: Context, action: Any) -> dict[str, Any]:
        try:
            return success_response(**action(resolve(ctx)))
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_browser_submit_transcript_run(
        ctx: Context,
        video_ids: list[str] | None = None,
        recent_limit: int = 20,
        force: bool = False,
        all_public: bool = False,
    ) -> dict[str, Any]:
        """提交指定/近期文案任务；all_public 仅用于用户显式全量回溯。"""

        def submit(container: Any) -> dict[str, Any]:
            if not container.settings.transcript_ingestion_enabled:
                raise AppError(
                    TRANSCRIPT_DISABLED,
                    "Transcript ingestion is disabled by TRANSCRIPT_INGESTION_ENABLED.",
                )
            if all_public and video_ids is not None:
                raise AppError(
                    VALIDATION_ERROR,
                    "all_public cannot be combined with explicit video_ids.",
                )
            ids = video_ids
            mode = "video_ids"
            trigger = "all_public_backfill" if all_public else "mcp"
            if all_public:
                rows = container.db.query_all(
                    "SELECT id FROM videos WHERE account_id=? AND is_active=1 "
                    "AND visibility='public' AND content_kind='video' "
                    "ORDER BY publish_time DESC,id",
                    (BROWSER_DEFAULT_ACCOUNT_ID,),
                    read_only=True,
                )
                if len(rows) > 100:
                    raise AppError(
                        VALIDATION_ERROR,
                        "all_public currently supports at most 100 videos per run; "
                        "submit explicit batches for larger libraries.",
                        extra={"public_video_count": len(rows), "per_run_limit": 100},
                    )
                ids = [str(row["id"]) for row in rows]
                mode = "all_public"
            elif ids is None:
                if not 1 <= recent_limit <= 100:
                    raise AppError(VALIDATION_ERROR, "recent_limit must be between 1 and 100.")
                rows = container.db.query_all(
                    "SELECT id FROM videos WHERE account_id=? AND is_active=1 "
                    "AND visibility='public' AND content_kind='video' "
                    "ORDER BY publish_time DESC,id LIMIT ?",
                    (BROWSER_DEFAULT_ACCOUNT_ID, recent_limit),
                    read_only=True,
                )
                ids = [str(row["id"]) for row in rows]
                mode = "recent"
            result = container.transcript_repository.create_run(
                BROWSER_DEFAULT_ACCOUNT_ID,
                ids,
                force=force,
                trigger=trigger,
                target_mode=mode,
            )
            container.transcript_coordinator.wake()
            return result

        return call(ctx, submit)

    @mcp.tool()
    def douyin_browser_get_transcript_run(
        ctx: Context,
        run_id: str,
        item_limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """读取某次提交的本地状态、逐视频阶段和逐 run 计数。"""
        def get(container: Any) -> dict[str, Any]:
            payload = _decode_run_cursor(
                container.transcript_query.signer,
                cursor,
                kind="run_items",
                expected_id=run_id,
                expected_account_id=BROWSER_DEFAULT_ACCOUNT_ID,
            )
            after = None
            if payload:
                try:
                    after = (
                        str(payload["attached_at"]),
                        str(payload["video_id"]),
                        str(payload["job_id"]),
                    )
                except KeyError as exc:
                    raise AppError(INVALID_CURSOR, "Run item cursor is incomplete.") from exc
            result = container.transcript_repository.get_run(
                run_id, item_limit=item_limit, after=after
            )
            if result["account_id"] != BROWSER_DEFAULT_ACCOUNT_ID:
                raise AppError(VALIDATION_ERROR, "Run does not belong to this account.")
            marker = result.pop("_next_item", None)
            if marker:
                result["next_cursor"] = container.transcript_query.signer.encode(
                    {
                        "v": 1,
                        "kind": "run_items",
                        "account_id": BROWSER_DEFAULT_ACCOUNT_ID,
                        "run_id": run_id,
                        **marker,
                    }
                )
            return result

        return call(ctx, get)

    @mcp.tool()
    def douyin_browser_list_transcript_runs(
        ctx: Context,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """分页列出本地文案任务。"""
        def list_page(container: Any) -> dict[str, Any]:
            payload = _decode_run_cursor(
                container.transcript_query.signer,
                cursor,
                kind="run_list",
                expected_id=BROWSER_DEFAULT_ACCOUNT_ID,
            )
            before = None
            if payload:
                try:
                    before = (str(payload["created_at"]), str(payload["id"]))
                except KeyError as exc:
                    raise AppError(INVALID_CURSOR, "Run list cursor is incomplete.") from exc
            result = container.transcript_repository.list_runs_page(
                BROWSER_DEFAULT_ACCOUNT_ID, limit, before
            )
            marker = result.pop("_next_run", None)
            result["next_cursor"] = (
                container.transcript_query.signer.encode(
                    {
                        "v": 1,
                        "kind": "run_list",
                        "account_id": BROWSER_DEFAULT_ACCOUNT_ID,
                        **marker,
                    }
                )
                if marker
                else None
            )
            return result

        return call(ctx, list_page)

    @mcp.tool()
    def douyin_browser_cancel_transcript_run(ctx: Context, run_id: str) -> dict[str, Any]:
        """取消这一 run 的需求，不影响其他 run 仍需要的共享工作。"""
        return call(ctx, lambda container: container.transcript_repository.cancel_run(run_id))

    @mcp.tool()
    def douyin_browser_retry_transcript_run(ctx: Context, run_id: str) -> dict[str, Any]:
        """为失败视频创建新的 retry run，旧 run 保持不可变。"""

        def retry(container: Any) -> dict[str, Any]:
            result = container.transcript_repository.retry_run(run_id)
            container.transcript_coordinator.wake()
            return result

        return call(ctx, retry)

    @mcp.tool()
    def douyin_browser_get_transcript_capabilities(ctx: Context) -> dict[str, Any]:
        """诊断功能门禁及本地 FFmpeg、FFprobe、模型配置。"""

        def capabilities(container: Any) -> dict[str, Any]:
            import shutil

            settings = container.settings
            model_dir = settings.transcript_asr_model_dir
            return {
                "ingestion_enabled": settings.transcript_ingestion_enabled,
                "pipeline_version": settings.transcript_pipeline_version,
                "ffmpeg_available": shutil.which(settings.transcript_ffmpeg_path) is not None,
                "ffprobe_available": shutil.which(settings.transcript_ffprobe_path) is not None,
                "asr_model_configured": bool(model_dir and model_dir.exists()),
                "worker_count": settings.transcript_worker_count,
                "auto_warmup_enabled": settings.transcript_auto_warmup_enabled,
                "warmup_recent_limit": settings.transcript_warmup_recent_limit,
                "auto_ingest_new_videos": settings.transcript_auto_ingest_new_videos,
                "auto_new_video_limit": settings.transcript_auto_new_video_limit,
                "auto_prepare_analysis": settings.transcript_auto_prepare_analysis,
            }

        return call(ctx, capabilities)

    @mcp.tool()
    def douyin_browser_get_transcript_backfill_plan(
        ctx: Context,
    ) -> dict[str, Any]:
        """预估全量历史回溯的数量、耗时和存储；不会创建文案任务。"""
        return call(
            ctx,
            lambda container: container.transcript_policy.backfill_plan(),
        )

    @mcp.tool()
    def douyin_browser_get_video_transcript(
        ctx: Context,
        video_id: str,
        revision: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
        include_raw_text: bool = False,
    ) -> dict[str, Any]:
        """分页返回不可变 revision 的原始时间戳分片。"""
        return call(
            ctx,
            lambda container: container.transcript_query.get_video_transcript(
                video_id,
                revision=revision,
                limit=limit,
                cursor=cursor,
                include_raw_text=include_raw_text,
            ),
        )

    @mcp.tool()
    def douyin_browser_get_video_analysis_context(
        ctx: Context,
        video_id: str,
        revision: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
        max_chars: int = 12000,
        auto_prepare: bool = True,
    ) -> dict[str, Any]:
        """返回分析段落；缺失时默认排队补齐且不改写原始 ASR 文本。"""
        def get_or_prepare(container: Any) -> dict[str, Any]:
            try:
                return container.transcript_query.get_video_analysis_context(
                    video_id,
                    revision=revision,
                    limit=limit,
                    cursor=cursor,
                    max_chars=max_chars,
                )
            except AppError as exc:
                should_prepare = (
                    exc.error_type == DATA_NOT_AVAILABLE
                    and revision is None
                    and cursor is None
                    and auto_prepare
                    and container.settings.transcript_auto_prepare_analysis
                )
                if not should_prepare:
                    raise
                return container.transcript_policy.prepare_analysis([video_id])

        return call(ctx, get_or_prepare)
