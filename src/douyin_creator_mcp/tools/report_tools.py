"""Summary and report MCP tools."""

from __future__ import annotations

from typing import Any

from ..responses import response_from_exception, success_response


def register_report_tools(mcp: Any, services: Any) -> None:
    @mcp.tool()
    def douyin_get_account_summary(
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        try:
            return success_response(
                **services.report_service.get_account_summary(account_id, start_date, end_date)
            )
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_generate_creator_report(account_id: str, period: str = "7d") -> dict[str, Any]:
        try:
            return success_response(**services.report_service.generate_creator_report(account_id, period))
        except Exception as exc:
            return response_from_exception(exc)
