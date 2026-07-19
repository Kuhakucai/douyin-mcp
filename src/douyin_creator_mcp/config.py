"""Runtime configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import CONFIGURATION_ERROR, AppError


@dataclass(slots=True)
class Settings:
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8787
    mcp_http_api_key: str | None = None
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
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
    transcript_ingestion_enabled: bool = False
    transcript_pipeline_version: str = "transcript-v1"
    transcript_worker_count: int = 1
    transcript_lease_seconds: int = 30
    transcript_heartbeat_seconds: int = 5
    transcript_max_attempts: int = 2
    transcript_response_max_bytes: int = 262_144
    transcript_default_max_chars: int = 12_000
    transcript_media_max_bytes: int = 512 * 1024 * 1024
    transcript_media_min_free_bytes: int = 1024 * 1024 * 1024
    transcript_bundle_min_observe_ms: int = 1500
    transcript_bundle_multi_stable_ms: int = 350
    transcript_bundle_single_stable_ms: int = 750
    transcript_bundle_max_observe_ms: int = 6000
    transcript_ffmpeg_path: str = "ffmpeg"
    transcript_ffprobe_path: str = "ffprobe"
    transcript_process_timeout_seconds: int = 600
    transcript_asr_model_dir: Path | None = None
    transcript_asr_model_size: str = "small"
    transcript_asr_device: str = "cpu"
    transcript_asr_compute_type: str = "int8"
    transcript_keep_reference_video: bool = False
    transcript_auto_warmup_enabled: bool = True
    transcript_warmup_recent_limit: int = 5
    transcript_auto_ingest_new_videos: bool = True
    transcript_auto_new_video_limit: int = 20
    transcript_auto_prepare_analysis: bool = True


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


def load_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | str = ".env",
) -> Settings:
    file_env = _read_dotenv(Path(dotenv_path))
    merged = dict(file_env)
    merged.update(os.environ if env is None else env)
    settings = Settings(
        mcp_transport=_get(merged, "MCP_TRANSPORT", "stdio").lower(),
        mcp_host=_get(merged, "MCP_HOST", "127.0.0.1"),
        mcp_port=_get_int(merged, "MCP_PORT", 8787),
        mcp_http_api_key=_get_optional(merged, "MCP_HTTP_API_KEY"),
        data_dir=Path(_get(merged, "DATA_DIR", "./data")),
        log_level=_get(merged, "LOG_LEVEL", "INFO"),
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
        transcript_ingestion_enabled=_get_bool(
            merged, "TRANSCRIPT_INGESTION_ENABLED", False
        ),
        transcript_pipeline_version=_get(
            merged, "TRANSCRIPT_PIPELINE_VERSION", "transcript-v1"
        ),
        transcript_worker_count=_get_int(merged, "TRANSCRIPT_WORKER_COUNT", 1),
        transcript_lease_seconds=_get_int(merged, "TRANSCRIPT_LEASE_SECONDS", 30),
        transcript_heartbeat_seconds=_get_int(
            merged, "TRANSCRIPT_HEARTBEAT_SECONDS", 5
        ),
        transcript_max_attempts=_get_int(merged, "TRANSCRIPT_MAX_ATTEMPTS", 2),
        transcript_response_max_bytes=_get_int(
            merged, "TRANSCRIPT_RESPONSE_MAX_BYTES", 262_144
        ),
        transcript_default_max_chars=_get_int(
            merged, "TRANSCRIPT_DEFAULT_MAX_CHARS", 12_000
        ),
        transcript_media_max_bytes=_get_int(
            merged, "TRANSCRIPT_MEDIA_MAX_BYTES", 512 * 1024 * 1024
        ),
        transcript_media_min_free_bytes=_get_int(
            merged, "TRANSCRIPT_MEDIA_MIN_FREE_BYTES", 1024 * 1024 * 1024
        ),
        transcript_bundle_min_observe_ms=_get_int(
            merged, "TRANSCRIPT_BUNDLE_MIN_OBSERVE_MS", 1500
        ),
        transcript_bundle_multi_stable_ms=_get_int(
            merged, "TRANSCRIPT_BUNDLE_MULTI_STABLE_MS", 350
        ),
        transcript_bundle_single_stable_ms=_get_int(
            merged, "TRANSCRIPT_BUNDLE_SINGLE_STABLE_MS", 750
        ),
        transcript_bundle_max_observe_ms=_get_int(
            merged, "TRANSCRIPT_BUNDLE_MAX_OBSERVE_MS", 6000
        ),
        transcript_ffmpeg_path=_get(merged, "TRANSCRIPT_FFMPEG_PATH", "ffmpeg"),
        transcript_ffprobe_path=_get(merged, "TRANSCRIPT_FFPROBE_PATH", "ffprobe"),
        transcript_process_timeout_seconds=_get_int(
            merged, "TRANSCRIPT_PROCESS_TIMEOUT_SECONDS", 600
        ),
        transcript_asr_model_dir=(
            Path(value)
            if (value := _get_optional(merged, "TRANSCRIPT_ASR_MODEL_DIR"))
            else None
        ),
        transcript_asr_model_size=_get(
            merged, "TRANSCRIPT_ASR_MODEL_SIZE", "small"
        ),
        transcript_asr_device=_get(merged, "TRANSCRIPT_ASR_DEVICE", "cpu"),
        transcript_asr_compute_type=_get(
            merged, "TRANSCRIPT_ASR_COMPUTE_TYPE", "int8"
        ),
        transcript_keep_reference_video=_get_bool(
            merged, "TRANSCRIPT_KEEP_REFERENCE_VIDEO", False
        ),
        transcript_auto_warmup_enabled=_get_bool(
            merged, "TRANSCRIPT_AUTO_WARMUP_ENABLED", True
        ),
        transcript_warmup_recent_limit=_get_int(
            merged, "TRANSCRIPT_WARMUP_RECENT_LIMIT", 5
        ),
        transcript_auto_ingest_new_videos=_get_bool(
            merged, "TRANSCRIPT_AUTO_INGEST_NEW_VIDEOS", True
        ),
        transcript_auto_new_video_limit=_get_int(
            merged, "TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT", 20
        ),
        transcript_auto_prepare_analysis=_get_bool(
            merged, "TRANSCRIPT_AUTO_PREPARE_ANALYSIS", True
        ),
    )
    _validate_transcript_settings(settings)
    return settings


def _validate_transcript_settings(settings: Settings) -> None:
    if not settings.transcript_pipeline_version.strip():
        raise AppError(CONFIGURATION_ERROR, "TRANSCRIPT_PIPELINE_VERSION cannot be empty.")
    bounded = {
        "TRANSCRIPT_WORKER_COUNT": (settings.transcript_worker_count, 1, 4),
        "TRANSCRIPT_LEASE_SECONDS": (settings.transcript_lease_seconds, 10, 300),
        "TRANSCRIPT_HEARTBEAT_SECONDS": (
            settings.transcript_heartbeat_seconds,
            1,
            60,
        ),
        "TRANSCRIPT_MAX_ATTEMPTS": (settings.transcript_max_attempts, 1, 10),
        "TRANSCRIPT_RESPONSE_MAX_BYTES": (
            settings.transcript_response_max_bytes,
            16_384,
            262_144,
        ),
        "TRANSCRIPT_DEFAULT_MAX_CHARS": (
            settings.transcript_default_max_chars,
            1_000,
            100_000,
        ),
        "TRANSCRIPT_MEDIA_MAX_BYTES": (
            settings.transcript_media_max_bytes,
            1_048_576,
            2 * 1024 * 1024 * 1024,
        ),
        "TRANSCRIPT_MEDIA_MIN_FREE_BYTES": (
            settings.transcript_media_min_free_bytes,
            0,
            20 * 1024 * 1024 * 1024,
        ),
        "TRANSCRIPT_PROCESS_TIMEOUT_SECONDS": (
            settings.transcript_process_timeout_seconds,
            10,
            3600,
        ),
        "TRANSCRIPT_WARMUP_RECENT_LIMIT": (
            settings.transcript_warmup_recent_limit,
            1,
            20,
        ),
        "TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT": (
            settings.transcript_auto_new_video_limit,
            1,
            100,
        ),
    }
    for name, (value, lower, upper) in bounded.items():
        if not lower <= value <= upper:
            raise AppError(
                CONFIGURATION_ERROR,
                f"{name} must be between {lower} and {upper}.",
            )
    if settings.transcript_heartbeat_seconds * 2 >= settings.transcript_lease_seconds:
        raise AppError(
            CONFIGURATION_ERROR,
            "TRANSCRIPT_HEARTBEAT_SECONDS must be less than half the lease.",
        )
    windows = (
        settings.transcript_bundle_multi_stable_ms,
        settings.transcript_bundle_single_stable_ms,
        settings.transcript_bundle_min_observe_ms,
        settings.transcript_bundle_max_observe_ms,
    )
    if not (0 < windows[0] <= windows[1] <= windows[2] <= windows[3] <= 30_000):
        raise AppError(
            CONFIGURATION_ERROR,
            "Bundle windows must satisfy 0 < multi <= single <= min <= max <= 30000.",
        )


def ensure_runtime_dirs(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "reports").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "exports").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "media").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "staging").mkdir(parents=True, exist_ok=True)
    settings.douyin_browser_profile_dir.mkdir(parents=True, exist_ok=True)


def validate_for_http(settings: Settings) -> None:
    if settings.mcp_transport == "http" and not settings.mcp_http_api_key:
        raise AppError(
            CONFIGURATION_ERROR,
            "MCP_HTTP_API_KEY is required when MCP_TRANSPORT=http.",
            retryable=False,
        )
