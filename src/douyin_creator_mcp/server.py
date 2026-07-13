"""MCP server entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api_mapping import load_api_mapping
from .config import Settings, ensure_runtime_dirs, load_settings, validate_for_http
from .errors import CONFIGURATION_ERROR, AppError
from .services.account_service import AccountService
from .services.auth_service import AuthService
from .services.browser_service import BrowserService
from .services.capability_service import CapabilityService
from .services.douyin_api import DouyinApiClient
from .services.report_service import ReportService
from .services.sync_service import SyncService
from .storage.db import Database
from .storage.token_store import TokenStore
from .tools.account_tools import register_account_tools
from .tools.auth_tools import register_auth_tools
from .tools.browser_tools import register_browser_tools
from .tools.report_tools import register_report_tools
from .tools.sync_tools import register_sync_tools


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    db: Database
    token_store: TokenStore
    api_client: DouyinApiClient
    account_service: AccountService
    auth_service: AuthService
    capability_service: CapabilityService
    sync_service: SyncService
    report_service: ReportService
    browser_service: BrowserService


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


def build_container(settings: Settings | None = None, http_client: Any | None = None) -> ServiceContainer:
    settings = settings or load_settings()
    validate_for_http(settings)
    ensure_runtime_dirs(settings)
    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    api_mapping = load_api_mapping(settings.api_mapping_file)
    token_store = TokenStore(db, settings.token_encryption_key)
    api_client = DouyinApiClient(settings, api_mapping, db, token_store, http_client=http_client)
    account_service = AccountService(db, api_client)
    capability_service = CapabilityService(db, api_mapping, account_service)
    auth_service = AuthService(settings, db, api_client, token_store, account_service)
    sync_service = SyncService(db, account_service, capability_service)
    report_service = ReportService(settings, db)
    browser_service = BrowserService(settings, db)
    return ServiceContainer(
        settings=settings,
        db=db,
        token_store=token_store,
        api_client=api_client,
        account_service=account_service,
        auth_service=auth_service,
        capability_service=capability_service,
        sync_service=sync_service,
        report_service=report_service,
        browser_service=browser_service,
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


def create_legacy_mcp(services: ServiceContainer | None = None) -> Any:
    """Compatibility entrypoint for the historical OpenAPI tool set."""

    services = services or build_container()
    mcp = _new_mcp()
    register_auth_tools(mcp, services)
    register_account_tools(mcp, services)
    register_sync_tools(mcp, services)
    register_report_tools(mcp, services)
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
