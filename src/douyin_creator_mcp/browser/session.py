"""Thin Playwright session wrapper for the browser-login channel."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings
from ..errors import (
    CONFIGURATION_ERROR,
    DATA_NOT_AVAILABLE,
    VIDEO_IDENTITY_UNRESOLVED,
    AppError,
)


def _load_sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AppError(
            CONFIGURATION_ERROR,
            "playwright is not installed. Run: python -m pip install -e \".[dev]\"",
            retryable=False,
        ) from exc
    return sync_playwright


class BrowserSession:
    """Manage a persistent local browser profile without exposing cookies."""

    def __init__(self, settings: Settings, headless: bool | None = None) -> None:
        self.settings = settings
        self._headless = settings.douyin_browser_headless if headless is None else headless
        self._playwright_manager: Any | None = None
        self._playwright: Any | None = None
        self._context: Any | None = None

    @property
    def is_running(self) -> bool:
        return self._context is not None

    @property
    def context(self) -> Any:
        if self._context is None:
            raise AppError(
                CONFIGURATION_ERROR,
                "Browser session is not started.",
                retryable=False,
            )
        return self._context

    def start(self) -> Any:
        if self._context is not None:
            return self._context

        self.settings.douyin_browser_profile_dir.mkdir(parents=True, exist_ok=True)

        manager_factory = _load_sync_playwright()
        self._playwright_manager = manager_factory()
        self._playwright = self._playwright_manager.start()

        launch_options: dict[str, Any] = {
            "user_data_dir": str(self.settings.douyin_browser_profile_dir),
            "headless": self._headless,
        }
        if self.settings.douyin_browser_channel:
            launch_options["channel"] = self.settings.douyin_browser_channel

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_options
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            self._playwright_manager = None
            raise
        return self._context

    def open_page(self, url: str, wait_until: str = "domcontentloaded") -> Any:
        page = self._page()
        page.goto(url, wait_until=wait_until)
        if self.settings.douyin_browser_page_settle_ms > 0:
            page.wait_for_timeout(self.settings.douyin_browser_page_settle_ms)
        return page

    def open_creator_home(self) -> Any:
        return self.open_page(self.settings.douyin_creator_home_url)

    def open_creator_video_page(self) -> Any:
        return self.open_page(self.settings.douyin_creator_video_url)

    def open_video_detail(self, url: str) -> Any:
        return self.open_page(url)

    def open_video_detail_from_list(self, title: str, publish_time: int) -> Any:
        """Open one uniquely matched card without touching its edit operations."""
        expected_title = str(title).strip()
        expected_time = datetime.fromtimestamp(
            int(publish_time), ZoneInfo("Asia/Shanghai")
        ).strftime("%Y年%m月%d日 %H:%M")
        if not expected_title:
            raise AppError(
                VIDEO_IDENTITY_UNRESOLVED,
                "作品标题为空，无法从作品列表确认详情身份。",
            )

        self._close_secondary_pages()
        list_page = self.open_creator_video_page()
        matches = self._find_detail_candidates(
            list_page,
            expected_title,
            expected_time,
        )

        if len(matches) != 1:
            observed_candidates = list_page.evaluate(
                """
                expected => {
                  const cards = [...document.querySelectorAll('[class*="video-card-content-"]')];
                  const normalized = cards.map(card => {
                    const titleNode = card.querySelector('[class*="info-title-text-"]') ||
                      card.querySelector('[class*="info-title-operation-"]');
                    const timeNode = card.querySelector('[class*="info-time-"]');
                    return {
                      title: titleNode ? (titleNode.innerText || '').trim() : '',
                      publish_time: timeNode ? (timeNode.innerText || '').trim() : ''
                    };
                  });
                  return {
                    card_count: cards.length,
                    same_time_titles: normalized
                      .filter(item => item.publish_time === expected.publish_time)
                      .map(item => item.title),
                    same_title_times: normalized
                      .filter(item => item.title === expected.title)
                      .map(item => item.publish_time)
                  };
                }
                """,
                {"title": expected_title, "publish_time": expected_time},
            )
            raise AppError(
                VIDEO_IDENTITY_UNRESOLVED,
                "无法用完整标题和发布时间唯一定位作品详情。",
                retryable=False,
                extra={
                    "expected_title": expected_title,
                    "expected_publish_time": expected_time,
                    "candidate_count": len(matches),
                    "observed_candidates": observed_candidates,
                },
            )

        click_target, detail_disabled = matches[0]
        if detail_disabled:
            raise AppError(
                DATA_NOT_AVAILABLE,
                "当前作品状态暂不支持查看详情数据。",
                retryable=False,
            )

        pages_before = list(self.context.pages)
        list_url_before = str(getattr(list_page, "url", "") or "")
        click_target.click(no_wait_after=True, timeout=5000)
        detail_page = self._wait_for_detail_page(
            list_page, pages_before, list_url_before
        )
        wait_for_load_state = getattr(detail_page, "wait_for_load_state", None)
        if callable(wait_for_load_state):
            wait_for_load_state("domcontentloaded")
        if self.settings.douyin_browser_page_settle_ms > 0:
            detail_page.wait_for_timeout(self.settings.douyin_browser_page_settle_ms)
        return detail_page

    def close(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        self._playwright_manager = None

        try:
            if context is not None:
                context.close()
        finally:
            if playwright is not None:
                playwright.stop()

    def _page(self) -> Any:
        context = self.start()
        pages = getattr(context, "pages", None) or []
        if pages:
            return pages[0]
        return context.new_page()

    def _close_secondary_pages(self) -> None:
        pages = list(getattr(self.start(), "pages", None) or [])
        for page in pages[1:]:
            try:
                page.close()
            except Exception:
                continue

    @staticmethod
    def _find_detail_candidates(
        page: Any,
        expected_title: str,
        expected_time: str,
        max_scrolls: int = 30,
    ) -> list[tuple[Any, bool]]:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        stable_rounds = 0
        previous_count = -1
        for _ in range(max_scrolls + 1):
            matches: list[tuple[Any, bool]] = []
            cards = page.locator('[class*="video-card-content-"]')
            matching_indices = page.evaluate(
                """
                expected => [...document.querySelectorAll('[class*="video-card-content-"]')]
                  .map((card, index) => {
                    const titleNode = card.querySelector('[class*="info-title-text-"]') ||
                      card.querySelector('[class*="info-title-operation-"]');
                    const title = titleNode ? (titleNode.innerText || '').trim() : '';
                    const text = (card.innerText || '').trim();
                    return title === expected.title && text.includes(expected.publish_time)
                      ? index
                      : null;
                  })
                  .filter(index => index !== null)
                """,
                {"title": expected_title, "publish_time": expected_time},
            )
            for index in matching_indices if isinstance(matching_indices, list) else []:
                card = cards.nth(index)
                title_nodes = card.locator('[class*="info-title-text-"]')
                if title_nodes.count() == 0:
                    title_nodes = card.locator('[class*="info-title-operation-"]')
                if title_nodes.count() == 0:
                    continue
                title_node = title_nodes.nth(0)
                operation_nodes = card.locator('[class*="info-title-operation-"]')
                container_class = str(
                    card.locator("..").get_attribute("class") or ""
                )
                matches.append(
                    (
                        operation_nodes.nth(0)
                        if operation_nodes.count() > 0
                        else title_node,
                        "disabled" in container_class,
                    )
                )
            if matches:
                return matches

            card_count = cards.count()
            total_count = page.evaluate(
                r"""
                () => {
                  const match = (document.body.innerText || '').match(/共\s*(\d+)\s*个作品/);
                  return match ? Number(match[1]) : null;
                }
                """
            )
            if total_count is not None and card_count >= int(total_count):
                break
            stable_rounds = stable_rounds + 1 if card_count <= previous_count else 0
            if stable_rounds >= 5:
                break
            previous_count = max(previous_count, card_count)
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(1000)
        return []

    def _wait_for_detail_page(
        self,
        list_page: Any,
        pages_before: list[Any],
        list_url_before: str,
    ) -> Any:
        for _ in range(100):
            pages = list(getattr(self.context, "pages", None) or [])
            new_pages = [page for page in pages if page not in pages_before]
            if new_pages:
                return new_pages[-1]
            if str(getattr(list_page, "url", "") or "") != str(
                list_url_before
            ):
                return list_page
            list_page.wait_for_timeout(100)
        page_urls = [
            str(getattr(page, "url", "") or "")
            for page in (getattr(self.context, "pages", None) or [])
        ]
        raise AppError(
            VIDEO_IDENTITY_UNRESOLVED,
            "点击作品标题后未检测到详情页。",
            retryable=True,
            extra={"observed_page_urls": page_urls},
        )

    def __enter__(self) -> BrowserSession:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
