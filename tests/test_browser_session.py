from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from douyin_creator_mcp.browser.session import BrowserSession
from douyin_creator_mcp.config import Settings


class FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, str | None]] = []
        self.wait_for_timeout_calls: list[int] = []

    def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append((url, wait_until))

    def wait_for_timeout(self, timeout: int) -> None:
        self.wait_for_timeout_calls.append(timeout)


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakeListNode:
    def __init__(self, page, index: int, kind: str = "card") -> None:
        self.page = page
        self.index = index
        self.kind = kind

    def count(self) -> int:
        return 1

    def nth(self, index: int):
        return self

    def locator(self, selector: str):
        if selector == "..":
            return FakeListNode(self.page, self.index, "container")
        if "info-title" in selector:
            return FakeListNode(self.page, self.index, "title")
        return FakeListNode(self.page, self.index, "other")

    def get_attribute(self, name: str):
        return "video-card" if name == "class" else None


class FakeCardsLocator:
    def __init__(self, page) -> None:
        self.page = page

    def count(self) -> int:
        return self.page.visible_count

    def nth(self, index: int) -> FakeListNode:
        return FakeListNode(self.page, index)


class FakeVirtualListPage:
    def __init__(self) -> None:
        self.records = [
            {"title": "A", "publish_time": "2026年07月01日 10:00"},
            {"title": "B", "publish_time": "2026年07月02日 10:00"},
            {"title": "C", "publish_time": "2026年07月03日 10:00"},
        ]
        self.visible_count = 1
        self.bottom_scrolls = 0

    def evaluate(self, script: str, argument=None):
        if script == "window.scrollTo(0, 0)":
            self.visible_count = 1
            return None
        if script.startswith("window.scrollTo(0, document.documentElement"):
            self.bottom_scrolls += 1
            self.visible_count = min(len(self.records), self.visible_count + 1)
            return None
        if "expected =>" in script:
            return [
                index
                for index, record in enumerate(self.records[: self.visible_count])
                if record == argument
            ]
        if "共\\s*" in script:
            return len(self.records)
        raise AssertionError(f"unexpected script: {script}")

    def locator(self, selector: str) -> FakeCardsLocator:
        return FakeCardsLocator(self)

    def wait_for_timeout(self, timeout: int) -> None:
        return None


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.launch_options: dict[str, object] | None = None

    def launch_persistent_context(self, **kwargs: object) -> FakeContext:
        self.launch_options = kwargs
        return self.context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> FakePlaywright:
        return self.playwright


def make_fake_stack() -> tuple[
    FakeContext,
    FakeChromium,
    FakePlaywright,
    FakePlaywrightManager,
]:
    context = FakeContext()
    chromium = FakeChromium(context)
    playwright = FakePlaywright(chromium)
    manager = FakePlaywrightManager(playwright)
    return context, chromium, playwright, manager


def make_settings(profile_root: str, **overrides: object) -> Settings:
    values = {"douyin_browser_profile_dir": Path(profile_root) / "profile"}
    values.update(overrides)
    return Settings(**values)


class BrowserSessionTests(unittest.TestCase):
    def test_detail_candidate_loader_reaches_lazy_loaded_cards(self) -> None:
        page = FakeVirtualListPage()

        matches = BrowserSession._find_detail_candidates(
            page,
            "C",
            "2026年07月03日 10:00",
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(page.bottom_scrolls, 2)

    def test_start_uses_persistent_profile_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, chromium, _playwright, manager = make_fake_stack()
            settings = Settings(
                douyin_browser_profile_dir=Path(tmp) / "profile",
                douyin_browser_headless=True,
                douyin_browser_channel="chrome",
            )

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                session = BrowserSession(settings)
                result = session.start()

            self.assertIs(result, context)
            self.assertTrue(session.is_running)
            self.assertEqual(
                chromium.launch_options,
                {
                    "user_data_dir": str(Path(tmp) / "profile"),
                    "headless": True,
                    "channel": "chrome",
                },
            )
            self.assertTrue((Path(tmp) / "profile").exists())

    def test_open_page_reuses_existing_page_and_goto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _chromium, _playwright, manager = make_fake_stack()
            existing_page = FakePage()
            context.pages.append(existing_page)
            settings = make_settings(tmp)

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                session = BrowserSession(settings)
                page = session.open_page(
                    "https://creator.douyin.com/",
                    wait_until="load",
                )

            self.assertIs(page, existing_page)
            self.assertEqual(
                existing_page.goto_calls,
                [("https://creator.douyin.com/", "load")],
            )
            self.assertEqual(existing_page.wait_for_timeout_calls, [5000])

    def test_open_page_creates_page_when_context_has_no_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _chromium, _playwright, manager = make_fake_stack()
            settings = make_settings(tmp)

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                session = BrowserSession(settings)
                page = session.open_creator_video_page()

            self.assertIs(page, context.pages[0])
            self.assertEqual(
                page.goto_calls,
                [(settings.douyin_creator_video_url, "domcontentloaded")],
            )

    def test_open_page_can_disable_settle_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _chromium, _playwright, manager = make_fake_stack()
            page = FakePage()
            context.pages.append(page)
            settings = make_settings(tmp, douyin_browser_page_settle_ms=0)

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                BrowserSession(settings).open_creator_home()

            self.assertEqual(page.wait_for_timeout_calls, [])

    def test_close_is_idempotent_and_stops_playwright(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _chromium, playwright, manager = make_fake_stack()
            settings = make_settings(tmp)

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                session = BrowserSession(settings)
                session.start()
                session.close()
                session.close()

            self.assertFalse(session.is_running)
            self.assertTrue(context.closed)
            self.assertTrue(playwright.stopped)

    def test_empty_channel_is_not_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _context, chromium, _playwright, manager = make_fake_stack()
            settings = Settings(
                douyin_browser_profile_dir=Path(tmp),
                douyin_browser_channel=None,
            )

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                BrowserSession(settings).start()

            self.assertNotIn("channel", chromium.launch_options or {})

    def test_context_manager_closes_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _chromium, playwright, manager = make_fake_stack()
            settings = make_settings(tmp)

            with patch(
                "douyin_creator_mcp.browser.session._load_sync_playwright",
                return_value=lambda: manager,
            ):
                with BrowserSession(settings) as session:
                    self.assertTrue(session.is_running)

            self.assertTrue(context.closed)
            self.assertTrue(playwright.stopped)


if __name__ == "__main__":
    unittest.main()
