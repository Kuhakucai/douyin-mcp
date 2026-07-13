import json
from pathlib import Path
import unittest

from douyin_creator_mcp.browser.extractors import (
    LOGGED_IN,
    LOGIN_REQUIRED,
    VERIFICATION_REQUIRED,
    detect_login_status,
    extract_page_snapshot,
    extract_detail_metrics,
    extract_structured_videos,
    extract_text_lines,
    extract_video_candidates,
    load_all_video_cards,
    collect_all_video_cards,
    detail_video_id_from_url,
    normalize_video_record,
    parse_duration_seconds,
    parse_metric_count,
    parse_metric_rate,
    parse_publish_time,
)


class FakePage:
    url = "https://creator.douyin.com/creator-micro/content/manage"

    def title(self) -> str:
        return "抖音创作者中心"

    def inner_text(self, selector: str, timeout: int | None = None) -> str:
        return "\n".join(
            [
                "创作者中心",
                "内容管理",
                "作品 A 播放 100 点赞 10 评论 2",
                "作品 B 审核中",
            ]
        )


class FakeEvaluatePage:
    def __init__(self, states=None, raw_records=None) -> None:
        self.states = list(states or [])
        self.raw_records = list(raw_records or [])
        self.scroll_count = 0
        self.wait_calls = []

    def evaluate(self, script: str):
        if script.startswith("window.scrollTo"):
            self.scroll_count += 1
            return None
        if "cards.push" in script:
            return self.raw_records
        return self.states.pop(0)

    def wait_for_timeout(self, timeout: int) -> None:
        self.wait_calls.append(timeout)


class VirtualListPage(FakeEvaluatePage):
    def __init__(self, batches) -> None:
        super().__init__()
        self.batches = batches
        self.index = 0

    def evaluate(self, script: str):
        if script.startswith("window.scrollTo"):
            self.scroll_count += 1
            self.index = min(self.index + 1, len(self.batches) - 1)
            return None
        if "cards.push" in script:
            return self.batches[self.index]
        return {"card_count": len(self.batches[self.index]), "total_count": 3}


class DetailPage:
    url = "https://creator.douyin.com/creator-micro/data/video/123"

    def title(self):
        return "作品数据"

    def inner_text(self, selector, timeout=None):
        return "创作者中心 Example A 数据详情"

    def evaluate(self, script):
        return {
            "曝光量": "1,000",
            "播放量": "800",
            "5秒完播率": "70%",
            "完播率": "31.5%",
            "平均播放时长": "12.5秒",
            "点赞量": "80",
            "收藏量": "20",
            "评论量": "10",
            "分享量": "5",
            "涨粉量": "3",
        }


class BrowserExtractorTests(unittest.TestCase):
    def test_detect_login_status_prioritizes_verification(self) -> None:
        status = detect_login_status("扫码登录 请完成验证 验证码")
        self.assertEqual(status, VERIFICATION_REQUIRED)

    def test_detect_login_required(self) -> None:
        status = detect_login_status("扫码登录 手机登录")
        self.assertEqual(status, LOGIN_REQUIRED)

    def test_verification_code_login_is_regular_login(self) -> None:
        status = detect_login_status("扫码登录 验证码登录 获取验证码")
        self.assertEqual(status, LOGIN_REQUIRED)

    def test_detect_logged_in_creator_page(self) -> None:
        status = detect_login_status(
            "创作者中心 内容管理",
            url="https://creator.douyin.com/",
            title="抖音创作者中心",
        )
        self.assertEqual(status, LOGGED_IN)

    def test_extract_text_lines_deduplicates_and_limits(self) -> None:
        lines = extract_text_lines(" A \nA\n\nB\nC", limit=2)
        self.assertEqual(lines, ["A", "B"])

    def test_extract_video_candidates_keeps_metric_like_lines(self) -> None:
        lines = ["首页", "作品 A 播放 100 点赞 10", "普通说明"]
        candidates = extract_video_candidates(lines)
        self.assertEqual(candidates, [{"text": "作品 A 播放 100 点赞 10"}])

    def test_extract_page_snapshot(self) -> None:
        snapshot = extract_page_snapshot(FakePage())
        self.assertEqual(snapshot["login_status"], LOGGED_IN)
        self.assertEqual(snapshot["title"], "抖音创作者中心")
        self.assertEqual(snapshot["source_url"], FakePage.url)
        self.assertEqual(len(snapshot["video_candidates"]), 2)

    def test_metric_count_units_and_missing_values(self) -> None:
        cases = {
            "1,291": 1291,
            "9.6万": 96000,
            "8.76万": 87600,
            "1.2亿": 120000000,
            "-": None,
            "unknown": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_metric_count(raw), expected)

    def test_duration_and_publish_time_parsing(self) -> None:
        self.assertEqual(parse_duration_seconds("01:05"), 65)
        self.assertEqual(parse_duration_seconds("01:02:03"), 3723)
        self.assertIsNone(parse_duration_seconds("01:99"))
        self.assertEqual(parse_publish_time("1970年01月01日 08:00"), 0)
        self.assertIsNone(parse_publish_time("not-a-date"))

    def test_extract_structured_videos_normalizes_fixture(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "browser_video_cards.json"
        raw_records = json.loads(fixture_path.read_text(encoding="utf-8"))

        records = extract_structured_videos(FakeEvaluatePage(raw_records=raw_records))

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["play_count"], 96000)
        self.assertEqual(records[0]["duration"], 65)
        self.assertEqual(records[0]["cover_url"], "https://example.test/covers/a.jpg")
        self.assertEqual(records[1]["collect_count"], 25000)
        self.assertIsNone(records[1]["cover_url"])

    def test_normalize_video_record_requires_title_and_publish_time(self) -> None:
        self.assertIsNone(normalize_video_record({"title": "", "publish_time": "bad"}))

    def test_load_all_video_cards_stops_when_total_reached(self) -> None:
        page = FakeEvaluatePage(
            states=[
                {"card_count": 12, "total_count": 24},
                {"card_count": 12, "total_count": 24},
                {"card_count": 24, "total_count": 24},
            ]
        )

        result = load_all_video_cards(page, wait_ms=250)

        self.assertEqual(result["loaded_card_count"], 24)
        self.assertEqual(result["stop_reason"], "total_reached")
        self.assertEqual(page.scroll_count, 2)
        self.assertEqual(page.wait_calls, [250, 250])

    def test_load_all_video_cards_stops_after_stable_rounds(self) -> None:
        page = FakeEvaluatePage(
            states=[
                {"card_count": 12, "total_count": None},
                {"card_count": 12, "total_count": None},
                {"card_count": 12, "total_count": None},
            ]
        )

        result = load_all_video_cards(page, stable_rounds=2, wait_ms=0)

        self.assertEqual(result["stop_reason"], "stable")
        self.assertEqual(result["scroll_rounds"], 2)

    def test_virtual_list_collects_cards_that_leave_the_dom(self) -> None:
        def raw(item_id, title, day):
            return {
                "platform_item_id": item_id,
                "title": title,
                "publish_time": f"2026年07月{day:02d}日 10:00",
                "duration": "00:10",
                "detail_url": f"https://creator.douyin.com/video/{item_id}",
                "metrics": {"播放": "10"},
            }

        page = VirtualListPage(
            [[raw("1", "A", 1), raw("2", "B", 2)], [raw("2", "B", 2), raw("3", "C", 3)]]
        )

        records, stats = collect_all_video_cards(page, wait_ms=0)

        self.assertEqual({item["platform_item_id"] for item in records}, {"1", "2", "3"})
        self.assertEqual(stats["stop_reason"], "total_reached")

    def test_detail_metrics_are_normalized_and_identity_is_confirmed(self) -> None:
        page = DetailPage()
        result = extract_detail_metrics(
            page,
            {"title": "Example A", "video_url": page.url, "platform_item_id": "123"},
        )

        self.assertTrue(result["identity_confirmed"])
        self.assertEqual(result["quality"], "complete")
        self.assertEqual(result["metrics"]["play_count"], 800)
        self.assertEqual(result["metrics"]["completion_rate"], 0.315)
        self.assertEqual(result["metrics"]["average_watch_duration_seconds"], 12.5)
        self.assertEqual(parse_metric_rate("31.5%"), 0.315)
        self.assertIsNone(parse_metric_rate("31.5"))

    def test_detail_identity_rejects_conflicting_url_ids(self) -> None:
        page = DetailPage()
        page.url = "https://creator.douyin.com/creator-micro/data?item_id=999999"
        result = extract_detail_metrics(
            page,
            {
                "title": "Another video",
                "video_url": "https://creator.douyin.com/creator-micro/data?item_id=123456",
                "item_id": "123456",
            },
        )

        self.assertFalse(result["identity_confirmed"])

    def test_work_detail_url_and_real_metric_aliases_are_supported(self) -> None:
        page = DetailPage()
        page.url = (
            "https://creator.douyin.com/creator-micro/work-management/"
            "work-detail/9000000000000000001?enter_from=list"
        )
        page.evaluate = lambda script: {
            "播放量": "8.76万",
            "完播率": "6.54%",
            "5s完播率": "43.21%",
            "平均播放时长": "11秒",
        }

        result = extract_detail_metrics(
            page,
            {"title": "Example A", "item_id": "9000000000000000001"},
        )

        self.assertEqual(
            detail_video_id_from_url(page.url), "9000000000000000001"
        )
        self.assertTrue(result["identity_confirmed"])
        self.assertEqual(result["metrics"]["play_count"], 87600)
        self.assertAlmostEqual(result["metrics"]["completion_rate"], 0.0654)
        self.assertAlmostEqual(
            result["metrics"]["five_second_completion_rate"], 0.4321
        )
        self.assertEqual(result["metrics"]["average_watch_duration_seconds"], 11.0)
        self.assertEqual(result["quality"], "complete")
        self.assertEqual(result["missing_reasons"]["exposure_count"], "not_displayed")


if __name__ == "__main__":
    unittest.main()
