from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from douyin_creator_mcp.config import Settings
from douyin_creator_mcp.errors import ACCOUNT_MISMATCH, AppError
from douyin_creator_mcp.services.browser_service import BrowserService
from douyin_creator_mcp.storage.db import Database


class FakePage:
    def __init__(
        self,
        url: str,
        text: str,
        title: str = "抖音创作者中心",
        raw_videos: list[dict] | None = None,
        total_count: int = 0,
        detail_metrics: dict | None = None,
    ) -> None:
        self.url = url
        self._text = text
        self._title = title
        self.raw_videos = list(raw_videos or [])
        self.total_count = total_count
        self.detail_metrics = detail_metrics

    def title(self) -> str:
        return self._title

    def inner_text(self, selector: str, timeout: int | None = None) -> str:
        return self._text

    def evaluate(self, script: str):
        if "detailMetrics" in script:
            return self.detail_metrics or {}
        if script.startswith("window.scrollTo"):
            return None
        if "cards.push" in script:
            return self.raw_videos
        return {
            "card_count": len(self.raw_videos),
            "total_count": self.total_count,
            "scroll_height": 1000,
        }

    def wait_for_timeout(self, timeout: int) -> None:
        return None


class FakeSession:
    def __init__(self, home_page: FakePage, video_page: FakePage, detail_page: FakePage | None = None) -> None:
        self.home_page = home_page
        self.video_page = video_page
        self.detail_page = detail_page
        self.pages: list[FakePage] = []
        self.is_running = False
        self.close_count = 0
        self.home_open_count = 0
        self.detail_list_calls: list[tuple[str, int]] = []

    @property
    def context(self) -> SimpleNamespace:
        return SimpleNamespace(pages=self.pages)

    def open_creator_home(self) -> FakePage:
        self.home_open_count += 1
        self.is_running = True
        self.pages = [self.home_page]
        return self.home_page

    def open_creator_video_page(self) -> FakePage:
        self.is_running = True
        self.pages = [self.video_page]
        return self.video_page

    def open_video_detail(self, url: str) -> FakePage:
        if self.detail_page is None:
            raise RuntimeError("detail page is not configured")
        self.is_running = True
        self.pages = [self.detail_page]
        return self.detail_page

    def open_video_detail_from_list(self, title: str, publish_time: int) -> FakePage:
        if self.detail_page is None:
            raise RuntimeError("detail page is not configured")
        self.detail_list_calls.append((title, publish_time))
        self.is_running = True
        self.pages = [self.detail_page]
        return self.detail_page

    def close(self) -> None:
        self.is_running = False
        self.close_count += 1


def make_db(tmp: str) -> Database:
    db = Database(Path(tmp) / "douyin.sqlite")
    db.init_schema()
    return db


def make_settings(tmp: str, auto_close: bool = True) -> Settings:
    return Settings(
        data_dir=Path(tmp),
        douyin_browser_profile_dir=Path(tmp) / "profile",
        douyin_browser_auto_close=auto_close,
    )


class BrowserServiceTests(unittest.TestCase):
    def test_login_status_returns_not_started_without_opening_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = BrowserService(make_settings(tmp), make_db(tmp))

            result = service.login_status()

            self.assertEqual(result["login_status"], "not_started")
            self.assertFalse(result["browser_running"])

    def test_login_start_opens_home_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                FakePage(
                    "https://creator.douyin.com/",
                    "创作者中心 内容管理",
                ),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理",
                ),
            )
            service = BrowserService(make_settings(tmp), make_db(tmp), lambda: session)

            result = service.login_start()

            self.assertTrue(result["browser_running"])
            self.assertEqual(result["login_status"], "logged_in")
            self.assertEqual(result["source_url"], "https://creator.douyin.com/")
            self.assertTrue((Path(tmp) / ".douyin-mcp.lock").exists())
            service.close_browser()
            self.assertFalse((Path(tmp) / ".douyin-mcp.lock").exists())

    def test_sync_creator_data_saves_snapshot_and_closes_when_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理\n作品 A 播放 100 点赞 10 评论 2",
                ),
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)

            result = service.sync_creator_data("acct-1")
            snapshot = db.query_one(
                "SELECT * FROM browser_snapshots WHERE id = ?",
                (result["snapshot_id"],),
            )
            job = db.query_one(
                "SELECT * FROM sync_jobs WHERE id = ?",
                (result["sync_job_id"],),
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["video_candidate_count"], 1)
            self.assertEqual(snapshot["account_id"], "acct-1")
            self.assertEqual(snapshot["status"], "logged_in")
            self.assertEqual(job["status"], "completed")
            self.assertEqual(session.close_count, 1)

    def test_sync_creator_data_keeps_browser_open_when_login_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "扫码登录"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "扫码登录 手机登录",
                    title="登录抖音",
                ),
            )
            service = BrowserService(make_settings(tmp), make_db(tmp), lambda: session)

            result = service.sync_creator_data("acct-1")

            self.assertEqual(result["status"], "user_action_required")
            self.assertEqual(result["login_status"], "login_required")
            self.assertEqual(session.close_count, 0)
            self.assertTrue(session.is_running)

    def test_background_login_expiry_falls_back_to_visible_login_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "扫码登录", title="登录抖音"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "扫码登录 手机登录",
                    title="登录抖音",
                ),
            )
            service = BrowserService(make_settings(tmp), make_db(tmp), lambda: session)

            result = service.sync_creator_data(mode="background_first")

            self.assertEqual(result["status"], "user_action_required")
            self.assertEqual(session.home_open_count, 1)
            self.assertTrue(session.is_running)
            self.assertEqual(session.close_count, 1)

    def test_refresh_report_uses_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理\n作品 A 播放 100 点赞 10 评论 2",
                ),
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)
            service.sync_creator_data("acct-1")

            result = service.refresh_report("acct-1")
            report_path = Path(result["report_path"])

            self.assertTrue(report_path.exists())
            self.assertEqual(result["summary"]["data_source"], "browser_snapshot")
            self.assertIn("浏览器登录态页面快照", report_path.read_text(encoding="utf-8"))

    def test_refresh_report_requires_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = BrowserService(make_settings(tmp), make_db(tmp))

            with self.assertRaises(AppError):
                service.refresh_report("missing")

    def test_latest_snapshot_summary_excludes_captured_text_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            db.execute(
                """
                INSERT INTO browser_snapshots
                  (id, account_id, source_url, title, status, extracted_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "snapshot-1",
                    "acct-1",
                    "https://creator.douyin.com/manage?temporary=value#section",
                    "Creator center",
                    "logged_in",
                    '{"text_lines":["private text"],"video_candidates":[{"text":"video"}]}',
                    "2026-07-11T00:00:00+00:00",
                ),
            )
            service = BrowserService(make_settings(tmp), db)

            result = service.latest_snapshot_summary("acct-1")

            self.assertEqual(result["snapshot_id"], "snapshot-1")
            self.assertEqual(result["text_line_count"], 1)
            self.assertEqual(result["video_candidate_count"], 1)
            self.assertEqual(result["source_url"], "https://creator.douyin.com/manage")
            self.assertNotIn("extracted", result)
            self.assertNotIn("text_lines", result)
            self.assertNotIn("video_candidates", result)

    def test_close_browser_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "creator center"),
                FakePage("https://creator.douyin.com/manage", "content management"),
            )
            service = BrowserService(make_settings(tmp), make_db(tmp), lambda: session)
            service.login_start()

            service.close_browser()
            service.close_browser()

            self.assertEqual(session.close_count, 1)

    def test_structured_sync_is_idempotent_and_updates_same_day_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            raw_videos = [
                {
                    "title": "Example A",
                    "publish_time": "2026年07月10日 18:30",
                    "duration": "01:05",
                    "status": "已发布",
                    "cover_url": "https://example.test/a.jpg?token=hidden",
                    "metrics": {"播放": "100", "点赞": "10", "评论": "2", "分享": "1"},
                },
                {
                    "title": "Example B",
                    "publish_time": "2026年07月09日 09:15",
                    "duration": "00:30",
                    "status": "已发布",
                    "metrics": {"播放": "200", "点赞": "20", "评论": "3", "分享": "2"},
                },
            ]
            video_page = FakePage(
                "https://creator.douyin.com/creator-micro/content/manage",
                "创作者中心 内容管理 作品管理",
                raw_videos=raw_videos,
                total_count=2,
            )
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                video_page,
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)

            first = service.sync_creator_data("acct-1")
            raw_videos[0]["metrics"]["播放"] = "150"
            video_page.raw_videos = raw_videos
            second = service.sync_creator_data("acct-1")

            videos = db.query_all("SELECT * FROM videos WHERE account_id = ?", ("acct-1",))
            metrics = db.query_all(
                "SELECT * FROM video_metrics WHERE account_id = ?", ("acct-1",)
            )
            listed = service.list_videos("acct-1", limit=1, offset=0)

            self.assertEqual(first["structured_video_count"], 2)
            self.assertEqual(second["videos_upserted"], 2)
            self.assertEqual(len(videos), 2)
            self.assertEqual(len(metrics), 2)
            self.assertEqual(listed["total"], 2)
            self.assertEqual(len(listed["videos"]), 1)
            self.assertEqual(listed["videos"][0]["latest_metrics"]["play_count"], 150)
            self.assertEqual(videos[0]["cover_url"].split("?")[0], videos[0]["cover_url"])

            binding = db.query_one(
                "SELECT * FROM browser_account_bindings WHERE account_id = ?",
                ("acct-1",),
            )
            self.assertIsNotNone(binding)
            self.assertNotIn("Example A", binding["anchor_hashes_json"])
            self.assertEqual(first["account_identity"]["status"], "bound")
            self.assertEqual(second["account_identity"]["status"], "verified")

    def test_account_fingerprint_rejects_switched_account_before_data_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            video_page = FakePage(
                "https://creator.douyin.com/creator-micro/content/manage",
                "创作者中心 内容管理 作品管理",
                raw_videos=[
                    {
                        "title": "Account A video",
                        "publish_time": "2026年7月10日 18:30",
                        "duration": "00:30",
                        "metrics": {"播放": "100"},
                    }
                ],
                total_count=1,
            )
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                video_page,
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)
            service.sync_creator_data()
            snapshot_count = db.query_one(
                "SELECT COUNT(*) AS count FROM browser_snapshots"
            )["count"]

            video_page.raw_videos = [
                {
                    "title": "Account B video",
                    "publish_time": "2026年7月11日 09:00",
                    "duration": "00:45",
                    "metrics": {"播放": "200"},
                }
            ]

            with self.assertRaises(AppError) as caught:
                service.sync_creator_data()

            self.assertEqual(caught.exception.error_type, ACCOUNT_MISMATCH)
            self.assertEqual(
                db.query_one("SELECT COUNT(*) AS count FROM videos")["count"],
                1,
            )
            self.assertEqual(
                db.query_one("SELECT COUNT(*) AS count FROM browser_snapshots")["count"],
                snapshot_count,
            )
            failed_job = db.query_one(
                "SELECT status, error_type FROM sync_jobs WHERE error_type = ? LIMIT 1",
                (ACCOUNT_MISMATCH,),
            )
            self.assertEqual(failed_job["status"], "failed")
            self.assertEqual(failed_job["error_type"], ACCOUNT_MISMATCH)

    def test_status_exposes_only_safe_account_and_lock_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            db.execute(
                "INSERT INTO browser_account_bindings "
                "(account_id, fingerprint_salt, anchor_hashes_json, anchor_count, "
                "created_at, last_verified_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "browser-default",
                    "secret-salt",
                    '["secret-anchor"]',
                    1,
                    "2026-07-13T00:00:00+00:00",
                    "2026-07-13T00:01:00+00:00",
                ),
            )
            service = BrowserService(make_settings(tmp), db)

            result = service.get_status()

            self.assertEqual(result["account_binding"]["bound"], True)
            self.assertNotIn("fingerprint_salt", result["account_binding"])
            self.assertNotIn("anchor_hashes_json", result["account_binding"])
            self.assertNotIn("path", result["profile_lock"])
            self.assertNotIn("pid", result["profile_lock"])
            self.assertNotIn("owner", result["profile_lock"])

    def test_v1_sync_reuses_legacy_browser_video_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            publish_time = 1783679400
            db.execute(
                """
                INSERT INTO videos
                  (id, account_id, title, publish_time, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("legacy-id", "browser-default", "Example A", publish_time, "browser_dom", "t", "t"),
            )
            raw_video = {
                "platform_item_id": "123456789",
                "title": "Example A",
                "publish_time": "2026年07月10日 18:30",
                "duration": "00:30",
                "detail_url": "https://creator.douyin.com/video/123456789",
                "metrics": {"播放": "10"},
            }
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理 作品管理",
                    raw_videos=[raw_video],
                    total_count=1,
                ),
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)

            service.sync_creator_data()

            rows = db.query_all("SELECT id, item_id FROM videos")
            self.assertEqual(rows, [{"id": "legacy-id", "item_id": "123456789"}])

    def test_list_videos_validates_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = BrowserService(make_settings(tmp), make_db(tmp))

            with self.assertRaises(AppError):
                service.list_videos(limit=0)
            with self.assertRaises(AppError):
                service.list_videos(offset=-1)
            with self.assertRaises(AppError):
                service.sync_creator_data(mode="invalid")
            with self.assertRaises(AppError):
                service.sync_if_needed(max_age_hours=-1)

    def test_detail_sync_persists_visible_metrics_and_derived_rates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            detail_url = "https://creator.douyin.com/creator-micro/data/video/123"
            raw_video = {
                "platform_item_id": "123",
                "title": "Example A",
                "publish_time": "2026年07月10日 18:30",
                "duration": "00:30",
                "detail_url": detail_url,
                "metrics": {"播放": "800", "点赞": "80", "评论": "10", "分享": "5", "收藏": "20"},
            }
            detail_metrics = {
                "曝光量": "1,000", "播放量": "800", "5秒完播率": "70%",
                "完播率": "30%", "平均播放时长": "12.5秒", "点赞量": "80",
                "收藏量": "20", "评论量": "10", "分享量": "5", "涨粉量": "3",
            }
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理 作品管理",
                    raw_videos=[raw_video],
                    total_count=1,
                ),
                FakePage(
                    detail_url,
                    "创作者中心 Example A 数据详情",
                    title="作品数据",
                    detail_metrics=detail_metrics,
                ),
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)
            service.sync_creator_data()
            video = db.query_one("SELECT id FROM videos WHERE item_id = '123'")

            result = service.sync_video_details(video_ids=[video["id"]], force=True)
            snapshot = db.query_one(
                "SELECT * FROM video_metric_snapshots WHERE video_id = ? AND source = 'browser_detail'",
                (video["id"],),
            )
            derived = db.query_one(
                "SELECT * FROM video_derived_metrics WHERE snapshot_id = ?", (snapshot["id"],)
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(snapshot["completion_rate"], 0.3)
            self.assertEqual(snapshot["average_watch_duration_seconds"], 12.5)
            self.assertEqual(derived["like_rate"], 0.1)
            self.assertEqual(snapshot["quality"], "complete")

            cached = service.sync_video_details(video_ids=[video["id"]])
            self.assertEqual(cached["cache_hits"], 1)
            self.assertEqual(cached["captured_at"], snapshot["captured_at"])
            self.assertEqual(
                db.query_one(
                    "SELECT COUNT(*) AS count FROM video_metric_snapshots "
                    "WHERE video_id = ? AND source = 'browser_detail'",
                    (video["id"],),
                )["count"],
                1,
            )

            db.execute(
                "UPDATE video_metric_snapshots SET parser_version = 'creator-detail-v1' "
                "WHERE id = ?",
                (snapshot["id"],),
            )
            refreshed = service.sync_video_details(video_ids=[video["id"]])
            self.assertEqual(refreshed["cache_hits"], 0)
            self.assertEqual(
                db.query_one(
                    "SELECT COUNT(*) AS count FROM video_metric_snapshots "
                    "WHERE video_id = ? AND source = 'browser_detail'",
                    (video["id"],),
                )["count"],
                2,
            )

    def test_detail_sync_resolves_missing_url_from_unique_list_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp)
            raw_video = {
                "title": "Example without URL",
                "publish_time": "2026年07月10日 18:30",
                "duration": "00:30",
                "metrics": {"播放": "800"},
            }
            detail_metrics = {
                "曝光量": "1,000",
                "播放量": "800",
                "5s完播率": "70%",
                "完播率": "30%",
                "平均播放时长": "12.5秒",
                "点赞量": "80",
                "收藏量": "20",
                "评论量": "10",
                "分享量": "5",
                "涨粉量": "3",
            }
            detail_url = (
                "https://creator.douyin.com/creator-micro/work-management/"
                "work-detail/9000000000000000001?enter_from=manage"
            )
            session = FakeSession(
                FakePage("https://creator.douyin.com/", "创作者中心"),
                FakePage(
                    "https://creator.douyin.com/creator-micro/content/manage",
                    "创作者中心 内容管理 作品管理",
                    raw_videos=[raw_video],
                    total_count=1,
                ),
                FakePage(
                    detail_url,
                    "创作者中心 Example without URL 数据详情",
                    title="作品数据",
                    detail_metrics=detail_metrics,
                ),
            )
            service = BrowserService(make_settings(tmp), db, lambda: session)
            service.sync_creator_data()
            video = db.query_one("SELECT * FROM videos")

            result = service.sync_video_details(video_ids=[video["id"]], force=True)
            updated = db.query_one("SELECT * FROM videos WHERE id = ?", (video["id"],))

            self.assertEqual(result["status"], "completed")
            self.assertEqual(
                session.detail_list_calls,
                [("Example without URL", video["publish_time"])],
            )
            self.assertEqual(updated["item_id"], "9000000000000000001")
            self.assertEqual(updated["video_id"], "9000000000000000001")
            self.assertEqual(updated["video_url"], detail_url.split("?")[0])

            session.detail_page.detail_metrics = {}
            degraded = service.sync_video_details(video_ids=[video["id"]], force=True)
            self.assertEqual(degraded["failures"][0]["reason"], "parser_degraded")
            self.assertEqual(
                db.query_one(
                    "SELECT COUNT(*) AS count FROM video_metric_snapshots "
                    "WHERE video_id = ? AND source = 'browser_detail'",
                    (video["id"],),
                )["count"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
