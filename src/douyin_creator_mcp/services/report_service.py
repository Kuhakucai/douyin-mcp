"""Creator account summary and Markdown reports."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import AUTHORIZATION_REQUIRED, AppError
from ..storage.db import Database


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ReportService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    @staticmethod
    def _period_to_dates(period: str) -> tuple[str, str]:
        today = date.today()
        days = 7
        if period.endswith("d") and period[:-1].isdigit():
            days = max(1, int(period[:-1]))
        start = today - timedelta(days=days - 1)
        return start.isoformat(), today.isoformat()

    def _account(self, account_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise AppError(
                AUTHORIZATION_REQUIRED,
                "Account is not authorized. Call douyin_auth_start first.",
                retryable=True,
            )
        return row

    def get_account_summary(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        account = self._account(account_id)
        capabilities = self.db.query_all(
            """
            SELECT capability_key, status, scope FROM api_capabilities
            WHERE account_id = ?
            ORDER BY capability_key
            """,
            (account_id,),
        )
        capability_map = {row["capability_key"]: row["status"] for row in capabilities}
        video_count = self.db.query_one("SELECT COUNT(*) AS count FROM videos WHERE account_id = ?", (account_id,))
        metric_totals = self.db.query_one(
            """
            SELECT
              SUM(play_count) AS total_play,
              SUM(like_count) AS total_like,
              SUM(comment_count) AS total_comment,
              SUM(share_count) AS total_share
            FROM video_metrics
            WHERE account_id = ?
            """,
            (account_id,),
        ) or {}
        missing_fields: list[str] = []
        if capability_map.get("video.data") != "available":
            missing_fields.extend(["play_count", "complete_rate", "avg_watch_duration"])
        if capability_map.get("fans.data") != "available":
            missing_fields.extend(["fans_trend", "fans_profile"])
        data_quality = "full" if not missing_fields else ("partial" if metric_totals.get("total_like") else "limited")
        return {
            "account": {
                "id": account["id"],
                "nickname": account.get("nickname"),
            },
            "date_range": {
                "start": start_date,
                "end": end_date,
            },
            "capabilities": capability_map,
            "summary": {
                "video_count": int(video_count["count"] if video_count else 0),
                "total_play": metric_totals.get("total_play"),
                "total_like": metric_totals.get("total_like"),
                "total_comment": metric_totals.get("total_comment"),
                "total_share": metric_totals.get("total_share"),
            },
            "analysis_notes": [
                "The report uses local cache and confirmed OpenAPI capabilities.",
                "Missing metrics indicate permission or API availability gaps, not poor account performance.",
            ],
            "data_quality": {
                "level": data_quality,
                "missing_fields": sorted(set(missing_fields)),
            },
        }

    def generate_creator_report(self, account_id: str, period: str = "7d") -> dict[str, Any]:
        start_date, end_date = self._period_to_dates(period)
        summary = self.get_account_summary(account_id, start_date, end_date)
        reports_dir = self.settings.data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{account_id}_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
        report_path = reports_dir / filename
        markdown = self._render_markdown(summary, period)
        report_path.write_text(markdown, encoding="utf-8")
        report_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO reports (id, account_id, period, date_start, date_end, report_path, summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                account_id,
                period,
                start_date,
                end_date,
                str(report_path),
                json.dumps(summary, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        return {
            "report_id": report_id,
            "report_path": str(report_path),
            "summary": summary,
        }

    @staticmethod
    def _render_markdown(summary: dict[str, Any], period: str) -> str:
        missing = summary["data_quality"]["missing_fields"]
        capabilities = summary["capabilities"]
        lines = [
            f"# 抖音账号复盘报告（{period}）",
            "",
            "## 数据来源",
            "",
            "- 本地 SQLite 缓存",
            "- 已确认可用的抖音开放平台接口",
            "",
            "## 数据时间范围",
            "",
            f"- 开始：{summary['date_range']['start']}",
            f"- 结束：{summary['date_range']['end']}",
            "",
            "## 账号概览",
            "",
            f"- 账号 ID：{summary['account']['id']}",
            f"- 昵称：{summary['account'].get('nickname') or '未同步'}",
            "",
            "## 能力状态",
            "",
        ]
        if capabilities:
            lines.extend(f"- {key}: {value}" for key, value in capabilities.items())
        else:
            lines.append("- 尚未完成能力探测")
        lines.extend(
            [
                "",
                "## 数据质量",
                "",
                f"- 等级：{summary['data_quality']['level']}",
                f"- 缺失指标：{', '.join(missing) if missing else '无'}",
                "",
                "## 账号表现总结",
                "",
                "当前报告基于已授权且已同步的数据生成。缺失指标表示权限或接口暂不可用，不代表账号表现不好。",
                "",
                "## 内容建议",
                "",
                "- 先完成 user_info 授权和账号基础信息同步。",
                "- 如果需要视频经营复盘，请在抖音开放平台申请 video.data 相关能力。",
                "- 如果需要粉丝趋势和画像，请申请 fans.data 相关能力。",
                "",
                "## 下一步权限补齐",
                "",
            ]
        )
        if missing:
            lines.extend(f"- 补齐 {field} 对应的 scope 或平台能力" for field in missing)
        else:
            lines.append("- 当前核心指标已满足报告生成要求")
        lines.append("")
        return "\n".join(lines)
