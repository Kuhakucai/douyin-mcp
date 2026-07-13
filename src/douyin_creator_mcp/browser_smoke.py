"""Command-line smoke runner for the browser-login channel."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from .browser.extractors import LOGGED_IN
from .config import ensure_runtime_dirs, load_settings
from .responses import response_from_exception, sanitize_payload
from .services.browser_service import BROWSER_DEFAULT_ACCOUNT_ID, BrowserService
from .storage.db import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douyin-browser-smoke",
        description="Run the Douyin creator browser-login channel locally.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        help="Open a visible browser and wait for login or verification.",
    )
    login_parser.add_argument("--timeout", type=float, default=180.0)
    login_parser.add_argument("--poll-interval", type=float, default=2.0)

    subparsers.add_parser(
        "status",
        help="Open the creator center briefly and inspect the saved profile login state.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Open the video management page and save a browser snapshot.",
    )
    sync_parser.add_argument(
        "--mode", choices=("visible", "background_first"), default="visible"
    )

    details_parser = subparsers.add_parser(
        "details",
        help="Collect detail metrics for recent or explicitly selected videos.",
    )
    details_parser.add_argument("--video-id", action="append", dest="video_ids")
    details_parser.add_argument("--recent-limit", type=int, default=20)
    details_parser.add_argument("--batch-size", type=int)
    details_parser.add_argument("--cursor", type=int, default=0)
    details_parser.add_argument("--force", action="store_true")
    details_parser.add_argument(
        "--mode", choices=("visible", "background_first"), default="visible"
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Generate a report from the latest saved browser snapshot.",
    )
    report_parser.add_argument("--period", default="latest")

    snapshot_parser = subparsers.add_parser(
        "latest-snapshot",
        help="Show safe metadata and counts for the latest browser snapshot.",
    )

    videos_parser = subparsers.add_parser(
        "videos",
        help="List synchronized structured videos and their latest visible metrics.",
    )
    videos_parser.add_argument("--limit", type=int, default=20)
    videos_parser.add_argument("--offset", type=int, default=0)
    return parser


def _validate_login_args(timeout: float, poll_interval: float) -> None:
    if timeout < 0:
        raise ValueError("--timeout must be greater than or equal to 0.")
    if poll_interval <= 0:
        raise ValueError("--poll-interval must be greater than 0.")


def _run_login(
    service: BrowserService,
    timeout: float,
    poll_interval: float,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    _validate_login_args(timeout, poll_interval)
    latest: dict[str, Any] = {}
    try:
        latest = service.login_start()
        if latest.get("login_status") == LOGGED_IN:
            return {"status": "success", "command": "login", "result": latest}

        poll_count = math.ceil(timeout / poll_interval)
        for poll_index in range(poll_count):
            remaining = timeout - poll_index * poll_interval
            sleep_fn(min(poll_interval, remaining))
            latest = service.login_status()
            if latest.get("login_status") == LOGGED_IN:
                return {"status": "success", "command": "login", "result": latest}

        return {
            "status": "error",
            "command": "login",
            "error_type": "login_timeout",
            "message": "Login was not detected before the timeout. Run login again to continue.",
            "retryable": True,
            "result": latest,
        }
    finally:
        service.close_browser()


def run_command(
    args: argparse.Namespace,
    service: BrowserService,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if args.command == "login":
        return _run_login(service, args.timeout, args.poll_interval, sleep_fn)

    if args.command == "status":
        try:
            result = service.login_start()
            return {"status": "success", "command": "status", "result": result}
        finally:
            service.close_browser()

    if args.command == "sync":
        try:
            result = service.sync_creator_data(mode=args.mode)
            return {"status": "success", "command": "sync", "result": result}
        finally:
            service.close_browser()

    if args.command == "details":
        try:
            result = service.sync_video_details(
                video_ids=args.video_ids,
                recent_limit=args.recent_limit,
                force=args.force,
                batch_size=args.batch_size,
                cursor=args.cursor,
                mode=args.mode,
            )
            return {"status": "success", "command": "details", "result": result}
        finally:
            service.close_browser()

    if args.command == "report":
        result = service.refresh_report(period=args.period)
        return {"status": "success", "command": "report", "result": result}

    if args.command == "latest-snapshot":
        result = service.latest_snapshot_summary()
        return {"status": "success", "command": "latest-snapshot", "result": result}

    if args.command == "videos":
        result = service.list_videos(limit=args.limit, offset=args.offset)
        return {"status": "success", "command": "videos", "result": result}

    raise ValueError(f"Unsupported command: {args.command}")


def build_service() -> BrowserService:
    settings = load_settings()
    ensure_runtime_dirs(settings)
    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    return BrowserService(settings, db)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = run_command(args, build_service())
    except Exception as exc:
        payload = response_from_exception(exc)
        payload["command"] = args.command

    sanitized = sanitize_payload(payload)
    print(json.dumps(sanitized, ensure_ascii=False, indent=2))
    return 0 if sanitized.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
