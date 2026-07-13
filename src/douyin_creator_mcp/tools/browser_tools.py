"""Browser-login MCP tools."""

from __future__ import annotations

from typing import Any

from ..responses import response_from_exception, success_response


def register_browser_tools(mcp: Any, services: Any) -> None:
    def call(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return success_response(
                **getattr(services.browser_service, method)(*args, **kwargs)
            )
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_browser_login_start() -> dict[str, Any]:
        """打开可见浏览器；仅在首次登录或登录过期时需要扫码。"""
        return call("login_start")

    @mcp.tool()
    def douyin_browser_login_status() -> dict[str, Any]:
        """查询当前可见浏览器会话中的登录状态。"""
        return call("login_status")

    @mcp.tool()
    def douyin_browser_get_status() -> dict[str, Any]:
        """读取本地缓存、同步任务、指标覆盖率和 profile 锁状态，不打开浏览器。"""
        return call("get_status")

    @mcp.tool()
    def douyin_browser_sync_if_needed(
        scope: str = "list",
        max_age_hours: int | None = None,
        mode: str = "background_first",
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        """仅在缓存过期时同步列表、详情或全部数据。"""
        return call(
            "sync_if_needed",
            scope=scope,
            max_age_hours=max_age_hours,
            mode=mode,
            recent_limit=recent_limit,
        )

    @mcp.tool()
    def douyin_browser_sync_creator_data(
        mode: str = "visible",
        force: bool = False,
    ) -> dict[str, Any]:
        """同步作品列表及页面可见的播放、点赞、评论和分享数据。"""
        return call("sync_creator_data", mode=mode, force=force)

    @mcp.tool()
    def douyin_browser_sync_video_details(
        video_ids: list[str] | None = None,
        recent_limit: int = 20,
        force: bool = False,
        batch_size: int | None = None,
        cursor: int = 0,
        mode: str = "visible",
    ) -> dict[str, Any]:
        """分批采集作品详情页的完播率、播放量、互动率等指标。"""
        return call(
            "sync_video_details",
            video_ids=video_ids,
            recent_limit=recent_limit,
            force=force,
            batch_size=batch_size,
            cursor=cursor,
            mode=mode,
        )

    @mcp.tool()
    def douyin_browser_list_videos(
        limit: int = 20,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        sort: str = "publish_time_desc",
    ) -> dict[str, Any]:
        """分页查询本地作品和最新列表指标快照。"""
        return call(
            "list_videos", limit=limit, offset=offset, filters=filters, sort=sort
        )

    @mcp.tool()
    def douyin_browser_get_video_performance(
        video_id: str,
        period: str = "30d",
    ) -> dict[str, Any]:
        """查询单条作品的列表、详情快照及派生指标。"""
        return call("get_video_performance", video_id=video_id, period=period)

    @mcp.tool()
    def douyin_browser_compare_videos(
        video_ids: list[str],
        metrics: list[str] | None = None,
        period: str = "30d",
    ) -> dict[str, Any]:
        """在相同来源和时间语义下对比 2 至 20 条作品。"""
        return call(
            "compare_videos", video_ids=video_ids, metrics=metrics, period=period
        )

    @mcp.tool()
    def douyin_browser_get_metric_coverage(period: str = "30d") -> dict[str, Any]:
        """查询关键指标覆盖率和数据质量警告。"""
        return call("get_metric_coverage", period=period)

    @mcp.tool()
    def douyin_browser_rank_video_potential(
        period: str = "30d",
        limit: int = 20,
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """按有版本的透明规则生成轻量潜力排序。"""
        return call(
            "rank_video_potential", period=period, limit=limit, weights=weights
        )

    @mcp.tool()
    def douyin_browser_generate_review(
        period: str = "30d",
        focus: str = "potential",
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        """为 Agent 返回带覆盖率、警告和证据引用的复盘上下文。"""
        return call(
            "generate_review", period=period, focus=focus, recent_limit=recent_limit
        )

    @mcp.tool()
    def douyin_browser_export_data(
        format: str = "json",
        period: str = "all",
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """导出本地快照和派生指标；V1 支持 JSON 与 CSV。"""
        return call(
            "export_data", format=format, period=period, output_path=output_path
        )
