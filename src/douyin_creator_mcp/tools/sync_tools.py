"""Data synchronization MCP tools."""

from __future__ import annotations

from typing import Any

from ..responses import response_from_exception, success_response


def register_sync_tools(mcp: Any, services: Any) -> None:
    @mcp.tool()
    def douyin_sync_available_data(account_id: str) -> dict[str, Any]:
        try:
            return success_response(**services.sync_service.sync_available_data(account_id))
        except Exception as exc:
            return response_from_exception(exc)
