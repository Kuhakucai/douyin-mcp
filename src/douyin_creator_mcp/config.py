"""Runtime configuration loading."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet

from .errors import CONFIGURATION_ERROR, AppError


@dataclass(slots=True)
class Settings:
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8787
    mcp_http_api_key: str | None = None
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    douyin_client_key: str | None = None
    douyin_client_secret: str | None = None
    douyin_redirect_uri: str = "https://your-domain.com/oauth/douyin/callback"
    douyin_scopes: tuple[str, ...] = ("user_info",)
    douyin_oauth_mode: str = "local_manual_code"
    token_encryption_key: str = ""
    http_timeout_seconds: int = 20
    sync_page_size: int = 20
    api_mapping_file: Path = Path("./docs/api-mapping.md")
    douyin_base_url: str = "https://open.douyin.com"
    douyin_browser_profile_dir: Path = Path("./data/browser-profile")
    douyin_browser_headless: bool = False
    douyin_browser_auto_close: bool = True
    douyin_browser_channel: str | None = "chrome"
    douyin_browser_page_settle_ms: int = 5000
    douyin_list_cache_ttl_hours: int = 24
    douyin_detail_cache_ttl_hours: int = 24
    douyin_detail_batch_size: int = 10
    douyin_profile_lock_filename: str = ".douyin-mcp.lock"
    douyin_list_parser_version: str = "creator-manage-v2"
    douyin_detail_parser_version: str = "creator-detail-v2"
    douyin_creator_home_url: str = "https://creator.douyin.com/"
    douyin_creator_video_url: str = "https://creator.douyin.com/creator-micro/content/manage"


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return env.get(key, default).strip()


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _get(env, key)
    return int(raw) if raw else default


def _get_optional(env: Mapping[str, str], key: str) -> str | None:
    value = _get(env, key)
    return value or None


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _get_scopes(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = _get(env, "DOUYIN_SCOPES", "user_info")
    scopes = tuple(scope.strip() for scope in raw.replace(";", ",").split(",") if scope.strip())
    return scopes or ("user_info",)


def load_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | str = ".env",
) -> Settings:
    file_env = _read_dotenv(Path(dotenv_path))
    merged = dict(file_env)
    merged.update(os.environ if env is None else env)
    return Settings(
        mcp_transport=_get(merged, "MCP_TRANSPORT", "stdio").lower(),
        mcp_host=_get(merged, "MCP_HOST", "127.0.0.1"),
        mcp_port=_get_int(merged, "MCP_PORT", 8787),
        mcp_http_api_key=_get_optional(merged, "MCP_HTTP_API_KEY"),
        data_dir=Path(_get(merged, "DATA_DIR", "./data")),
        log_level=_get(merged, "LOG_LEVEL", "INFO"),
        douyin_client_key=_get_optional(merged, "DOUYIN_CLIENT_KEY"),
        douyin_client_secret=_get_optional(merged, "DOUYIN_CLIENT_SECRET"),
        douyin_redirect_uri=_get(
            merged,
            "DOUYIN_REDIRECT_URI",
            "https://your-domain.com/oauth/douyin/callback",
        ),
        douyin_scopes=_get_scopes(merged),
        douyin_oauth_mode=_get(merged, "DOUYIN_OAUTH_MODE", "local_manual_code"),
        token_encryption_key=_get(merged, "TOKEN_ENCRYPTION_KEY", ""),
        http_timeout_seconds=_get_int(merged, "HTTP_TIMEOUT_SECONDS", 20),
        sync_page_size=_get_int(merged, "SYNC_PAGE_SIZE", 20),
        api_mapping_file=Path(_get(merged, "API_MAPPING_FILE", "./docs/api-mapping.md")),
        douyin_base_url=_get(merged, "DOUYIN_BASE_URL", "https://open.douyin.com"),
        douyin_browser_profile_dir=Path(
            _get(merged, "DOUYIN_BROWSER_PROFILE_DIR", "./data/browser-profile")
        ),
        douyin_browser_headless=_get_bool(merged, "DOUYIN_BROWSER_HEADLESS", False),
        douyin_browser_auto_close=_get_bool(merged, "DOUYIN_BROWSER_AUTO_CLOSE", True),
        douyin_browser_channel=_get_optional(merged, "DOUYIN_BROWSER_CHANNEL") or "chrome",
        douyin_browser_page_settle_ms=max(
            0, _get_int(merged, "DOUYIN_BROWSER_PAGE_SETTLE_MS", 5000)
        ),
        douyin_list_cache_ttl_hours=max(
            0, _get_int(merged, "DOUYIN_LIST_CACHE_TTL_HOURS", 24)
        ),
        douyin_detail_cache_ttl_hours=max(
            0, _get_int(merged, "DOUYIN_DETAIL_CACHE_TTL_HOURS", 24)
        ),
        douyin_detail_batch_size=min(
            10, max(1, _get_int(merged, "DOUYIN_DETAIL_BATCH_SIZE", 10))
        ),
        douyin_profile_lock_filename=_get(
            merged, "DOUYIN_PROFILE_LOCK_FILENAME", ".douyin-mcp.lock"
        ),
        douyin_list_parser_version=_get(
            merged, "DOUYIN_LIST_PARSER_VERSION", "creator-manage-v2"
        ),
        douyin_detail_parser_version=_get(
            merged, "DOUYIN_DETAIL_PARSER_VERSION", "creator-detail-v2"
        ),
        douyin_creator_home_url=_get(
            merged,
            "DOUYIN_CREATOR_HOME_URL",
            "https://creator.douyin.com/",
        ),
        douyin_creator_video_url=_get(
            merged,
            "DOUYIN_CREATOR_VIDEO_URL",
            "https://creator.douyin.com/creator-micro/content/manage",
        ),
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "reports").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "exports").mkdir(parents=True, exist_ok=True)
    settings.douyin_browser_profile_dir.mkdir(parents=True, exist_ok=True)


def validate_for_auth(settings: Settings) -> None:
    missing = [
        name
        for name, value in {
            "DOUYIN_CLIENT_KEY": settings.douyin_client_key,
            "DOUYIN_CLIENT_SECRET": settings.douyin_client_secret,
            "DOUYIN_REDIRECT_URI": settings.douyin_redirect_uri,
            "TOKEN_ENCRYPTION_KEY": settings.token_encryption_key,
        }.items()
        if not value
    ]
    if missing:
        raise AppError(
            CONFIGURATION_ERROR,
            "Missing required auth configuration: " + ", ".join(missing),
            retryable=False,
        )
    if settings.douyin_oauth_mode not in {"local_manual_code", "https_callback"}:
        raise AppError(
            CONFIGURATION_ERROR,
            "DOUYIN_OAUTH_MODE must be local_manual_code or https_callback.",
            retryable=False,
        )


def validate_for_http(settings: Settings) -> None:
    if settings.mcp_transport == "http" and not settings.mcp_http_api_key:
        raise AppError(
            CONFIGURATION_ERROR,
            "MCP_HTTP_API_KEY is required when MCP_TRANSPORT=http.",
            retryable=False,
        )


def normalize_fernet_key(raw_key: str) -> bytes:
    value = raw_key.strip()
    if not value:
        raise AppError(CONFIGURATION_ERROR, "TOKEN_ENCRYPTION_KEY is required.")
    try:
        Fernet(value.encode("utf-8"))
        return value.encode("utf-8")
    except Exception:
        digest = sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)


def generate_token_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def main() -> None:
    print(generate_token_key())


if __name__ == "__main__":
    main()
