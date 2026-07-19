"""Unified command-line interface for the lightweight browser MCP."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .browser.extractors import LOGGED_IN
from .compliance import (
    PLATFORM_COMPLIANCE_NOTICE,
    platform_compliance_status,
    record_platform_risk_acknowledgement,
)
from .config import ensure_runtime_dirs, load_settings
from .responses import error_response, response_from_exception, sanitize_payload, success_response
from .services.browser_service import BrowserService
from .storage.db import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douyin-mcp",
        description="抖音创作者中心单账号本地数据 MCP。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化目录和数据库，并输出 MCP 配置示例。")
    commands.add_parser("doctor", help="检查本地运行环境，不打开浏览器。")

    acknowledge = commands.add_parser(
        "acknowledge-platform-risk",
        help="确认已阅读平台自动化访问风险说明。",
    )
    acknowledge.add_argument(
        "--yes",
        action="store_true",
        help="明确确认已阅读并理解平台条款风险。",
    )

    login = commands.add_parser("login", help="打开浏览器并等待扫码登录。")
    login.add_argument("--timeout", type=float, default=180.0)
    login.add_argument("--poll-interval", type=float, default=2.0)
    commands.add_parser("status", help="查看缓存、同步、覆盖率和登录状态。")

    sync = commands.add_parser("sync", help="同步作品列表。")
    sync.add_argument("--mode", choices=("visible", "background_first"), default="visible")
    sync.add_argument("--force", action="store_true")

    details = commands.add_parser("details", help="分批同步作品详情指标。")
    details.add_argument("--video-id", action="append", dest="video_ids")
    details.add_argument("--recent-limit", type=int, default=20)
    details.add_argument("--batch-size", type=int)
    details.add_argument("--cursor", type=int, default=0)
    details.add_argument("--mode", choices=("visible", "background_first"), default="visible")
    details.add_argument("--force", action="store_true")

    videos = commands.add_parser("videos", help="查询本地作品列表。")
    videos.add_argument("--limit", type=int, default=20)
    videos.add_argument("--offset", type=int, default=0)
    videos.add_argument("--sort", default="publish_time_desc")

    performance = commands.add_parser("performance", help="查询单条作品指标。")
    performance.add_argument("video_id")
    performance.add_argument("--period", default="30d")

    export = commands.add_parser("export", help="导出本地指标快照。")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--period", default="all")
    export.add_argument("--output")

    purge = commands.add_parser("purge", help="删除数据库、报告、导出和专用浏览器 profile。")
    purge.add_argument("--yes", action="store_true", help="确认不可恢复地删除本地数据。")
    return parser


def build_service() -> BrowserService:
    settings = load_settings()
    ensure_runtime_dirs(settings)
    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    return BrowserService(settings, db)


def _mcp_config(service: BrowserService) -> dict[str, Any]:
    environment = {
        "MCP_TRANSPORT": "stdio",
        "DATA_DIR": str(service.settings.data_dir.resolve()),
        "DOUYIN_BROWSER_PROFILE_DIR": str(
            service.settings.douyin_browser_profile_dir.resolve()
        ),
        "TRANSCRIPT_INGESTION_ENABLED": str(
            service.settings.transcript_ingestion_enabled
        ).lower(),
        "TRANSCRIPT_AUTO_WARMUP_ENABLED": str(
            service.settings.transcript_auto_warmup_enabled
        ).lower(),
        "TRANSCRIPT_WARMUP_RECENT_LIMIT": str(
            service.settings.transcript_warmup_recent_limit
        ),
        "TRANSCRIPT_AUTO_INGEST_NEW_VIDEOS": str(
            service.settings.transcript_auto_ingest_new_videos
        ).lower(),
        "TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT": str(
            service.settings.transcript_auto_new_video_limit
        ),
        "TRANSCRIPT_AUTO_PREPARE_ANALYSIS": str(
            service.settings.transcript_auto_prepare_analysis
        ).lower(),
    }
    if service.settings.transcript_asr_model_dir is not None:
        environment["TRANSCRIPT_ASR_MODEL_DIR"] = str(
            service.settings.transcript_asr_model_dir.resolve()
        )
    return {
        "mcpServers": {
            "douyin-creator": {
                "command": str(Path(sys.executable).resolve()),
                "args": ["-m", "douyin_creator_mcp.server"],
                "env": environment,
            }
        },
        "client_toml": "\n".join(
            [
                "[mcp_servers.douyin_creator]",
                f'command = {json.dumps(str(Path(sys.executable).resolve()))}',
                'args = ["-m", "douyin_creator_mcp.server"]',
                "",
                "[mcp_servers.douyin_creator.env]",
                *[
                    f"{key} = {json.dumps(value)}"
                    for key, value in environment.items()
                ],
            ]
        ),
    }


def _login(
    service: BrowserService,
    timeout: float,
    poll_interval: float,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    if timeout < 0 or poll_interval <= 0:
        raise ValueError("timeout must be >= 0 and poll-interval must be > 0.")
    try:
        latest = service.login_start()
        if latest.get("login_status") == LOGGED_IN:
            return success_response(**latest)
        for index in range(math.ceil(timeout / poll_interval)):
            sleep_fn(min(poll_interval, timeout - index * poll_interval))
            latest = service.login_status()
            if latest.get("login_status") == LOGGED_IN:
                return success_response(**latest)
        return error_response(
            "login_timeout",
            "在等待时间内未检测到登录，请重新执行 login。",
            retryable=True,
            login_status=latest.get("login_status"),
        )
    finally:
        service.close_browser()


def run_command(
    args: argparse.Namespace,
    service: BrowserService,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if args.command == "init":
        return success_response(
            schema_version=service.db.schema_version(),
            data_dir=str(service.settings.data_dir.resolve()),
            profile_dir=str(service.settings.douyin_browser_profile_dir.resolve()),
            backup_path=str(service.db.last_backup_path) if service.db.last_backup_path else None,
            mcp_config=_mcp_config(service),
            platform_compliance=platform_compliance_status(
                service.settings.data_dir
            ),
        )
    if args.command == "acknowledge-platform-risk":
        if not args.yes:
            return error_response(
                "confirmation_required",
                "请先阅读 PLATFORM_COMPLIANCE.md；确认理解风险后加 --yes。",
                retryable=True,
                platform_compliance=platform_compliance_status(
                    service.settings.data_dir
                ),
            )
        return success_response(
            platform_compliance=record_platform_risk_acknowledgement(
                service.settings.data_dir
            )
        )
    if args.command == "doctor":
        checks = {
            "schema": service.db.schema_version(),
            "data_dir_writable": service.settings.data_dir.exists()
            and os.access(service.settings.data_dir, os.W_OK),
            "profile_dir_ready": service.settings.douyin_browser_profile_dir.exists()
            and os.access(service.settings.douyin_browser_profile_dir, os.W_OK),
            "playwright_installed": importlib.util.find_spec("playwright") is not None,
            "browser_channel": bool(service.settings.douyin_browser_channel),
        }
        return success_response(checks=checks, ready=all(bool(value) for value in checks.values()))
    if args.command == "login":
        return _login(service, args.timeout, args.poll_interval, sleep_fn)
    if args.command == "status":
        return success_response(**service.get_status())
    if args.command == "sync":
        return success_response(**service.sync_creator_data(mode=args.mode, force=args.force))
    if args.command == "details":
        return success_response(
            **service.sync_video_details(
                video_ids=args.video_ids,
                recent_limit=args.recent_limit,
                force=args.force,
                batch_size=args.batch_size,
                cursor=args.cursor,
                mode=args.mode,
            )
        )
    if args.command == "videos":
        return success_response(
            **service.list_videos(limit=args.limit, offset=args.offset, sort=args.sort)
        )
    if args.command == "performance":
        return success_response(
            **service.get_video_performance(args.video_id, period=args.period)
        )
    if args.command == "export":
        return success_response(
            **service.export_data(
                format=args.format,
                period=args.period,
                output_path=args.output,
            )
        )
    if args.command == "purge":
        if not args.yes:
            return error_response(
                "confirmation_required",
                "purge 会删除全部本地数据和扫码登录 profile；确认后请加 --yes。",
                retryable=True,
            )
        return success_response(**service.purge_local_data())
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {
        "init",
        "acknowledge-platform-risk",
        "login",
        "sync",
        "details",
    }:
        print(f"平台合规提示：{PLATFORM_COMPLIANCE_NOTICE}", file=sys.stderr)
    try:
        payload = run_command(args, build_service())
    except Exception as exc:
        payload = response_from_exception(exc)
    sanitized = sanitize_payload(payload)
    print(json.dumps(sanitized, ensure_ascii=False, indent=2))
    return 0 if sanitized.get("ok") else 1


def _configure_utf8_console() -> None:
    """Keep Chinese titles and emoji printable on legacy Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


if __name__ == "__main__":
    sys.exit(main())
