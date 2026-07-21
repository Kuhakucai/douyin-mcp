"""Priority command executor that exclusively owns every synchronous browser object."""

from __future__ import annotations

import itertools
import hashlib
import json
import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import is_dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..browser.extractors import (
    collect_all_video_cards,
    extract_detail_metrics,
    extract_page_snapshot,
)
from ..browser.profile_lock import ProfileLock
from ..browser.session import BrowserSession
from ..config import Settings
from ..content.models import EphemeralRequest, MediaCandidate
from ..errors import CONFIGURATION_ERROR, VALIDATION_ERROR, AppError
from ..errors import ACCOUNT_IDENTITY_UNRESOLVED, ACCOUNT_MISMATCH
from ..storage.db import Database
from .media_observer import BundleWindows, MediaBundleCollector
from .commands import (
    BrowserCommand,
    CloseSession,
    LoginStart,
    LoginStatus,
    ObserveMediaBundle,
    Shutdown,
    SyncCreatorList,
    SyncVideoDetails,
    VerifyAccount,
)


class BrowserBackend(Protocol):
    def handle(self, command: BrowserCommand) -> Any: ...

    def close(self) -> None: ...


def _format_creator_publish_time(timestamp: int) -> str:
    """Format creator-card time without relying on the process C locale."""
    published = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai"))
    return (
        f"{published.year:04d}年{published.month:02d}月{published.day:02d}日 "
        f"{published.hour:02d}:{published.minute:02d}"
    )


class DefaultBrowserBackend:
    """Default handler; constructed and used only inside the executor thread."""

    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database
        self.session: BrowserSession | None = None
        self.profile_lock: ProfileLock | None = None
        self.thread_id = threading.get_ident()
        self._verified_targets: dict[str, dict[str, Any]] = {}

    def _ensure_session(self, headless: bool | None = None) -> BrowserSession:
        if self.session is None:
            lock = ProfileLock(
                self.settings.douyin_browser_profile_dir,
                self.settings.douyin_profile_lock_filename,
            )
            lock.acquire()
            self.profile_lock = lock
            self.session = BrowserSession(self.settings, headless=headless)
        return self.session

    def handle(self, command: BrowserCommand) -> Any:
        if isinstance(command, LoginStart):
            session = self._ensure_session(headless=command.headless)
            snapshot = extract_page_snapshot(session.open_creator_home())
            return {
                "browser_running": session.is_running,
                "login_status": snapshot["login_status"],
                "title": snapshot["title"],
                "source_url": snapshot["source_url"],
                "video_candidate_count": len(snapshot["video_candidates"]),
            }
        if isinstance(command, LoginStatus):
            if self.session is None or not self.session.is_running:
                return {"browser_running": False, "login_status": "not_started"}
            pages = list(getattr(self.session.context, "pages", None) or [])
            if not pages:
                return {"browser_running": True, "login_status": "unknown"}
            snapshot = extract_page_snapshot(pages[0])
            return {
                "browser_running": True,
                "login_status": snapshot["login_status"],
                "title": snapshot["title"],
                "source_url": snapshot["source_url"],
                "video_candidate_count": len(snapshot["video_candidates"]),
            }
        if isinstance(command, SyncCreatorList):
            session = self._ensure_session(headless=command.headless)
            page = session.open_creator_video_page()
            initial = extract_page_snapshot(page)
            videos: list[dict[str, Any]] = []
            load_stats: dict[str, Any] = {}
            if initial["login_status"] == "logged_in":
                videos, load_stats = collect_all_video_cards(page)
            snapshot = extract_page_snapshot(page, videos, load_stats)
            return {"snapshot": snapshot, "videos": videos, "load_stats": load_stats}
        if isinstance(command, SyncVideoDetails):
            session = self._ensure_session(headless=command.headless)
            details: list[dict[str, Any]] = []
            for video in command.videos:
                had_url = bool(video.get("video_url"))
                page = (
                    session.open_video_detail(str(video["video_url"]))
                    if had_url
                    else session.open_video_detail_from_list(
                        str(video.get("title") or ""), int(video["publish_time"])
                    )
                )
                details.append(
                    {
                        "video_id": video["id"],
                        "source_url": str(getattr(page, "url", "") or ""),
                        "had_detail_url": had_url,
                        "detail": extract_detail_metrics(page, video),
                    }
                )
            return {"details": details}
        if isinstance(command, VerifyAccount):
            session = self._ensure_session()
            page = session.open_creator_video_page()
            videos, load_stats = collect_all_video_cards(page)
            declared = load_stats.get("page_total_video_count")
            loaded = int(load_stats.get("loaded_card_count") or 0)
            if declared is not None and loaded != int(declared):
                raise AppError(
                    VALIDATION_ERROR,
                    "Creator inventory is incomplete; target identity was not frozen.",
                    retryable=True,
                )
            self._verify_account_binding(command.account_id, videos)
            expected = command.expected_video or {}
            matches = self._matching_videos(videos, expected)
            if command.target_video_id and len(matches) != 1:
                raise AppError(
                    VALIDATION_ERROR,
                    "Current creator card does not uniquely match the requested video.",
                )
            if matches:
                match = matches[0]
                if match.get("visibility") != "public" or match.get("content_kind") != "video":
                    raise AppError(
                        VALIDATION_ERROR,
                        "Only a currently public video card may enter media observation.",
                    )
                self._verified_targets[str(command.target_video_id)] = match
            return {
                "target_verified": bool(matches),
                "declared_count": declared,
                "collected_count": loaded,
            }
        if isinstance(command, ObserveMediaBundle):
            target = self._verified_targets.get(command.target_video_id)
            if target is None:
                raise AppError(
                    VALIDATION_ERROR,
                    "Target must be verified immediately before media observation.",
                )
            return self._observe_media(command, target)
        if isinstance(command, CloseSession):
            self.close()
            return {"closed": True}
        if isinstance(command, Shutdown):
            self.close()
            return {"shutdown": True}
        raise AppError(VALIDATION_ERROR, f"Unsupported browser command: {type(command).__name__}")

    def _verify_account_binding(
        self, account_id: str, videos: list[dict[str, Any]]
    ) -> None:
        """Verify the persisted account binding on the browser-owner thread."""
        if self.database is None:
            raise AppError(
                ACCOUNT_IDENTITY_UNRESOLVED,
                "Account identity storage is unavailable.",
            )
        binding = self.database.query_one(
            "SELECT fingerprint_salt,anchor_hashes_json "
            "FROM browser_account_bindings WHERE account_id=?",
            (account_id,),
            read_only=True,
        )
        sources = []
        for video in videos:
            title = str(video.get("title") or "").strip()
            published = str(video.get("publish_time") or "").strip()
            if title and published:
                sources.append(
                    hashlib.sha256(f"{published}|{title}".encode("utf-8")).hexdigest()
                )
        if binding is None or not sources:
            raise AppError(
                ACCOUNT_IDENTITY_UNRESOLVED,
                "The current creator account cannot be verified against its local binding.",
                retryable=not sources,
            )
        salt = str(binding.get("fingerprint_salt") or "")
        try:
            stored = {
                str(value)
                for value in json.loads(binding.get("anchor_hashes_json") or "[]")
                if value
            }
        except (TypeError, ValueError):
            stored = set()
        if not salt or not stored:
            raise AppError(
                ACCOUNT_IDENTITY_UNRESOLVED,
                "The local creator account binding is incomplete.",
            )
        current = {
            hashlib.sha256(f"{salt}|{source}".encode("utf-8")).hexdigest()
            for source in sources
        }
        if not stored.intersection(current):
            raise AppError(
                ACCOUNT_MISMATCH,
                "The signed-in creator account does not match the requested account.",
            )

    def close(self) -> None:
        session, self.session = self.session, None
        lock, self.profile_lock = self.profile_lock, None
        try:
            if session is not None:
                session.close()
        finally:
            if lock is not None:
                lock.release()

    @staticmethod
    def _matching_videos(
        videos: list[dict[str, Any]], expected: dict[str, Any]
    ) -> list[dict[str, Any]]:
        platform_id = str(
            expected.get("platform_item_id")
            or expected.get("item_id")
            or expected.get("video_id")
            or ""
        )
        if platform_id:
            id_matches = [
                item
                for item in videos
                if str(item.get("platform_item_id") or "") == platform_id
            ]
            if id_matches:
                return id_matches
            # Creator cards do not consistently expose a detail link/platform id.
            # Fall back only when the otherwise matching card has no platform id;
            # an explicit, different id remains a hard mismatch.
            return [
                item
                for item in videos
                if not str(item.get("platform_item_id") or "")
                and item.get("title") == expected.get("title")
                and int(item.get("publish_time") or 0)
                == int(expected.get("publish_time") or -1)
            ]
        return [
            item
            for item in videos
            if item.get("title") == expected.get("title")
            and int(item.get("publish_time") or 0)
            == int(expected.get("publish_time") or -1)
        ]

    def _observe_media(
        self, command: ObserveMediaBundle, target: dict[str, Any]
    ) -> Any:
        session = self._ensure_session()
        page = session.open_creator_video_page()
        collect_all_video_cards(page)
        title = str(target.get("title") or "")
        publish_time = _format_creator_publish_time(int(target["publish_time"]))
        index = page.evaluate(
            """
            expected => [...document.querySelectorAll('[class*="video-card-content-"]')]
              .findIndex(card => {
                const title = card.querySelector('[class*="info-title-text-"]');
                const time = card.querySelector('[class*="info-time-"]');
                return title && (title.innerText || '').trim() === expected.title &&
                  time && (time.innerText || '').trim() === expected.publish_time;
              })
            """,
            {"title": title, "publish_time": publish_time},
        )
        if not isinstance(index, int) or index < 0:
            raise AppError(VALIDATION_ERROR, "Verified target card disappeared.")
        card = page.locator('[class*="video-card-content-"]').nth(index)
        cover = card.locator('[class*="video-card-cover-"]')
        if cover.count() != 1:
            raise AppError(VALIDATION_ERROR, "Target card has no unique preview cover.")
        events: list[tuple[float, str, Any]] = []
        action_started = time.monotonic()

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", "") or "")
            host = (urlsplit(url).hostname or "").lower()
            if host.endswith(".douyinvod.com") or host == "douyinvod.com":
                try:
                    request_frame = request.frame
                except Exception:
                    request_frame = None
                events.append((time.monotonic(), url, request_frame))

        page.on("request", on_request)
        iframe = None
        try:
            cover.click(timeout=5000)
            iframe = page.locator('iframe[src*="/creatorvideo/"]')
            iframe.wait_for(state="attached", timeout=10_000)
            iframe_url = str(iframe.get_attribute("src") or "")
            observed = re.search(r"/creatorvideo/(\d+)", iframe_url)
            observed_platform_id = observed.group(1) if observed else None
            iframe_handle = iframe.element_handle()
            preview_frame = (
                iframe_handle.content_frame() if iframe_handle is not None else None
            )
            if observed_platform_id is None or preview_frame is None:
                raise AppError(
                    VALIDATION_ERROR,
                    "Preview did not expose verifiable frame and playback identity.",
                )
            expected_platform_id = str(target.get("platform_item_id") or "") or None
            if (
                expected_platform_id
                and observed_platform_id
                and expected_platform_id != observed_platform_id
            ):
                raise AppError(VALIDATION_ERROR, "Preview platform identity changed.")
            user_agent = str(page.evaluate("() => navigator.userAgent"))
            windows = BundleWindows(
                self.settings.transcript_bundle_min_observe_ms,
                self.settings.transcript_bundle_multi_stable_ms,
                self.settings.transcript_bundle_single_stable_ms,
                self.settings.transcript_bundle_max_observe_ms,
            )
            if command.full_window:
                windows.min_observe_ms = windows.max_observe_ms
            collector = MediaBundleCollector(
                command.target_video_id,
                expected_platform_id or observed_platform_id,
                "creator-preview-v1",
                windows=windows,
            )
            collector.start(action_started)
            seen: set[str] = set()
            deadline = action_started + windows.max_observe_ms / 1000
            while time.monotonic() <= deadline:
                for observed_at, url, request_frame in events:
                    if not self._request_has_preview_evidence(
                        request_frame, preview_frame, observed_at, action_started
                    ):
                        continue
                    identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    if identifier in seen:
                        continue
                    parsed = urlsplit(url)
                    query = parse_qs(parsed.query)
                    url_video_id = (query.get("__vid") or [None])[0]
                    if (
                        expected_platform_id
                        and url_video_id
                        and str(url_video_id) != expected_platform_id
                    ):
                        continue
                    bitrate = (query.get("br") or query.get("bt") or [None])[0]
                    seen.add(identifier)
                    collector.observe(
                        MediaCandidate(
                            identifier,
                            command.target_video_id,
                            expected_platform_id or observed_platform_id,
                            f"browser-{self.thread_id}",
                            f"page-{id(page)}",
                            hashlib.sha256(
                                f"{iframe_url}|{id(preview_frame)}".encode("utf-8")
                            ).hexdigest(),
                            f"creatorvideo:{observed_platform_id}:{id(preview_frame)}",
                            observed_at,
                            None,
                            int(bitrate) if bitrate and str(bitrate).isdigit() else None,
                            None,
                            None,
                            EphemeralRequest.from_values(
                                url,
                                {"User-Agent": user_agent, "Referer": iframe_url},
                            ),
                        )
                    )
                bundle = collector.build(time.monotonic())
                if bundle is not None and bundle.candidates:
                    return bundle
                page.wait_for_timeout(100)
            bundle = collector.build(time.monotonic())
            if bundle is None or not bundle.candidates:
                raise AppError(
                    CONFIGURATION_ERROR,
                    "No approved media request was observed before the bundle timeout.",
                    retryable=True,
                )
            return bundle
        finally:
            remove_listener = getattr(page, "remove_listener", None)
            if callable(remove_listener):
                remove_listener("request", on_request)
            try:
                close = page.locator('[class*="esc-box-"]')
                if close.count() == 1:
                    close.click(timeout=5000)
                if iframe is not None and iframe.count() > 0:
                    iframe.wait_for(state="detached", timeout=5000)
            except Exception:
                pass

    @staticmethod
    def _request_has_preview_evidence(
        request_frame: Any,
        preview_frame: Any,
        observed_at: float,
        action_started: float,
    ) -> bool:
        return (
            request_frame is not None
            and request_frame is preview_frame
            and observed_at >= action_started
        )


class BrowserExecutor:
    def __init__(
        self,
        settings: Settings,
        backend_factory: Any | None = None,
        *,
        database: Database | None = None,
        name: str = "douyin-browser-executor",
    ):
        self.settings = settings
        self.backend_factory = backend_factory or (
            lambda: DefaultBrowserBackend(settings, database)
        )
        self.name = name
        self._queue: queue.PriorityQueue[tuple[int, int, BrowserCommand, Future[Any]]] = (
            queue.PriorityQueue()
        )
        self._sequence = itertools.count()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._cancelled: set[str] = set()
        self._cancel_lock = threading.Lock()
        self.thread_id: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        if not self._started.wait(10):
            raise RuntimeError("BrowserExecutor failed to start.")

    def submit(self, command: BrowserCommand) -> Future[Any]:
        self.start()
        future: Future[Any] = Future()
        self._queue.put((command.priority, next(self._sequence), command, future))
        return future

    def execute(self, command: BrowserCommand, timeout: float | None = None) -> Any:
        remaining = max(0.0, command.deadline_monotonic - time.monotonic())
        return self.submit(command).result(timeout=remaining if timeout is None else timeout)

    def cancel(self, command_id: str) -> None:
        with self._cancel_lock:
            self._cancelled.add(command_id)

    def close_session(self) -> None:
        self.execute(CloseSession())

    def shutdown(self, timeout: float = 10.0) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            self.execute(Shutdown(deadline_monotonic=time.monotonic() + timeout), timeout)
        finally:
            thread.join(timeout)
            self._thread = None
        if thread.is_alive():
            raise RuntimeError("BrowserExecutor did not stop.")

    def _run(self) -> None:
        self.thread_id = threading.get_ident()
        backend = self.backend_factory()
        self._started.set()
        stop = False
        try:
            while not stop:
                _, _, command, future = self._queue.get()
                if future.cancelled():
                    continue
                with self._cancel_lock:
                    cancelled = command.command_id in self._cancelled
                    self._cancelled.discard(command.command_id)
                if cancelled:
                    future.cancel()
                    continue
                if time.monotonic() > command.deadline_monotonic:
                    future.set_exception(TimeoutError("Browser command deadline exceeded."))
                    continue
                try:
                    result = backend.handle(command)
                    self._validate_pure_value(result)
                    future.set_result(result)
                    stop = isinstance(command, Shutdown)
                except BaseException as exc:
                    future.set_exception(exc)
        finally:
            try:
                backend.close()
            finally:
                self._stopped.set()

    @classmethod
    def _validate_pure_value(cls, value: Any) -> None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("Browser result dictionary keys must be strings.")
                cls._validate_pure_value(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                cls._validate_pure_value(item)
            return
        if is_dataclass(value) and value.__class__.__module__.startswith(
            "douyin_creator_mcp"
        ):
            return
        raise TypeError(f"Browser result contains non-pure value: {type(value).__name__}")
