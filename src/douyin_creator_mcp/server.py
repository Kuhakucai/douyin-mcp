"""MCP server entrypoint with side-effect-free construction."""

from __future__ import annotations

from typing import Any

from .config import Settings, load_settings, validate_for_http
from .errors import CONFIGURATION_ERROR, AppError
from .runtime import Runtime, ServiceContainer
from .tools.browser_tools import register_browser_tools
from .tools.transcript_tools import register_transcript_tools

BrowserServiceContainer = ServiceContainer


def _fastmcp(runtime: Runtime) -> Any:
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise AppError(
            CONFIGURATION_ERROR,
            "fastmcp is not installed. Run: python -m pip install -e .",
        ) from exc
    return FastMCP("douyin_creator_mcp", lifespan=runtime.lifespan)


def create_mcp(
    services: ServiceContainer | None = None,
    *,
    settings: Settings | None = None,
    runtime: Runtime | None = None,
) -> Any:
    """Build tool definitions without touching DATA_DIR or starting threads."""
    if services is not None:
        # Tests and embedders may supply an already-running container.
        runtime = runtime or Runtime(services.settings)
        runtime.container = services
    else:
        settings = settings or load_settings()
        validate_for_http(settings)
        runtime = runtime or Runtime(settings)
    mcp = _fastmcp(runtime)
    register_browser_tools(mcp, services=services)
    register_transcript_tools(mcp, services=services)
    mcp._douyin_runtime = runtime
    return mcp


def build_browser_container(settings: Settings | None = None) -> ServiceContainer:
    """Compatibility helper for code that explicitly wants an active container.

    Normal server startup must use ``create_mcp`` so lifespan owns cleanup.
    """
    raise AppError(
        CONFIGURATION_ERROR,
        "Direct container construction is no longer supported; use Runtime.lifespan.",
    )


def main() -> None:
    settings = load_settings()
    validate_for_http(settings)
    mcp = create_mcp(settings=settings)
    kwargs: dict[str, Any] = {"transport": settings.mcp_transport}
    if settings.mcp_transport == "http":
        kwargs.update({"host": settings.mcp_host, "port": settings.mcp_port})
    mcp.run(**kwargs)


if __name__ == "__main__":
    main()
