"""Authorization MCP tools."""

from __future__ import annotations

from typing import Any

from ..responses import response_from_exception, success_response


def register_auth_tools(mcp: Any, services: Any) -> None:
    @mcp.tool()
    def douyin_auth_start(scopes: list[str] | None = None) -> dict[str, Any]:
        try:
            return success_response(**services.auth_service.start_auth(scopes))
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_auth_complete(code: str, state: str | None = None) -> dict[str, Any]:
        try:
            return success_response(**services.auth_service.complete_auth(code, state))
        except Exception as exc:
            return response_from_exception(exc)

    @mcp.tool()
    def douyin_auth_status(auth_session_id: str | None = None) -> dict[str, Any]:
        try:
            return success_response(**services.auth_service.get_auth_status(auth_session_id))
        except Exception as exc:
            return response_from_exception(exc)
