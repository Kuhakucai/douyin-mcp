"""MCP server entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings, ensure_runtime_dirs, load_settings, validate_for_http
from .errors import CONFIGURATION_ERROR, AppError
from .services.browser_service import BrowserService
from .storage.db import Database
from .tools.browser_tools import register_browser_tools


@dataclass(slots=True)
class BrowserServiceContainer:
    """Lightweight default container for the single-account browser channel."""

    settings: Settings
    db: Database
    browser_service: BrowserService


def build_browser_container(settings: Settings | None = None) -> BrowserServiceContainer:
    settings = settings or load_settings()
    validate_for_http(settings)
    ensure_runtime_dirs(settings)
    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    return BrowserServiceContainer(
        settings=settings,
        db=db,
        browser_service=BrowserService(settings, db),
    )


def _new_mcp() -> Any:
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise AppError(
            CONFIGURATION_ERROR,
            "fastmcp is not installed. Run: python -m pip install -e .",
            retryable=False,
        ) from exc
    return FastMCP("douyin_creator_mcp")


def create_mcp(services: BrowserServiceContainer | None = None) -> Any:
    """Create the browser-only V1 server exposed to normal users and Agents."""

    services = services or build_browser_container()
    mcp = _new_mcp()
    register_browser_tools(mcp, services)
    return mcp


def main() -> None:
    services = build_browser_container()
    mcp = create_mcp(services)
    kwargs: dict[str, Any] = {"transport": services.settings.mcp_transport}
    if services.settings.mcp_transport == "http":
        kwargs.update({"host": services.settings.mcp_host, "port": services.settings.mcp_port})
    mcp.run(**kwargs)


if __name__ == "__main__":
    main()
