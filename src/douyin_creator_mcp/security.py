"""MCP HTTP access control helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from .config import Settings, validate_for_http
from .errors import MCP_ACCESS_DENIED, AppError


def require_http_api_key(headers: Mapping[str, str], settings: Settings) -> None:
    validate_for_http(settings)
    expected = settings.mcp_http_api_key
    if settings.mcp_transport != "http":
        return
    normalized = {key.lower(): value for key, value in headers.items()}
    api_key = normalized.get("x-api-key")
    authorization = normalized.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else None
    if expected and (api_key == expected or bearer == expected):
        return
    raise AppError(MCP_ACCESS_DENIED, "MCP HTTP API key is invalid or missing.", retryable=False)


class McpAccessMiddleware:
    """Small ASGI middleware for deployments that expose MCP over HTTP."""

    def __init__(self, app: Callable[..., Awaitable[Any]], settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin1"): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        try:
            require_http_api_key(headers, self.settings)
        except AppError:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"status":"error","error_type":"mcp_access_denied","message":"MCP HTTP API key is invalid or missing.","retryable":false}',
                }
            )
            return
        await self.app(scope, receive, send)
