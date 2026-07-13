"""Account and capability MCP tools."""

from __future__ import annotations

from typing import Any

from ..responses import response_from_exception, success_response


def register_account_tools(mcp: Any, services: Any) -> None:
    @mcp.tool()
    def douyin_list_accounts() -> dict[str, Any]:
        try:
            return success_response(accounts=services.account_service.list_accounts())
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_get_account_profile(account_id: str) -> dict[str, Any]:
        try:
            return success_response(account=services.account_service.get_account_profile(account_id))
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_check_capabilities(account_id: str) -> dict[str, Any]:
        try:
            return success_response(**services.capability_service.check_capabilities(account_id))
        except Exception as exc:
            return response_from_exception(exc)
