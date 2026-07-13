from types import SimpleNamespace
import inspect
import unittest

from douyin_creator_mcp.tools.browser_tools import register_browser_tools


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class FakeBrowserService:
    def login_start(self):
        return {"login_status": "logged_in"}

    def login_status(self):
        return {"login_status": "not_started"}

    def get_status(self):
        return {"status": "completed", "coverage": {}}

    def sync_if_needed(self, **kwargs):
        return {"status": "cache_hit", **kwargs}

    def sync_creator_data(self, **kwargs):
        return {"snapshot_id": "snap-1", **kwargs}

    def sync_video_details(self, **kwargs):
        return {"job_id": "job-1", **kwargs}

    def list_videos(self, **kwargs):
        return {**kwargs, "videos": []}

    def get_video_performance(self, **kwargs):
        return kwargs

    def compare_videos(self, **kwargs):
        return kwargs

    def get_metric_coverage(self, **kwargs):
        return kwargs

    def rank_video_potential(self, **kwargs):
        return kwargs

    def generate_review(self, **kwargs):
        return kwargs

    def export_data(self, **kwargs):
        return kwargs


class BrowserToolsTests(unittest.TestCase):
    def test_register_browser_tools(self) -> None:
        mcp = FakeMCP()
        services = SimpleNamespace(browser_service=FakeBrowserService())

        register_browser_tools(mcp, services)

        self.assertEqual(
            set(mcp.tools),
            {
                "douyin_browser_login_start",
                "douyin_browser_login_status",
                "douyin_browser_get_status",
                "douyin_browser_sync_if_needed",
                "douyin_browser_sync_creator_data",
                "douyin_browser_sync_video_details",
                "douyin_browser_list_videos",
                "douyin_browser_get_video_performance",
                "douyin_browser_compare_videos",
                "douyin_browser_get_metric_coverage",
                "douyin_browser_rank_video_potential",
                "douyin_browser_generate_review",
                "douyin_browser_export_data",
            },
        )
        self.assertEqual(
            mcp.tools["douyin_browser_login_start"]()["status"],
            "success",
        )
        self.assertEqual(
            mcp.tools["douyin_browser_sync_creator_data"]()["snapshot_id"],
            "snap-1",
        )
        self.assertEqual(
            mcp.tools["douyin_browser_sync_video_details"](recent_limit=5)[
                "recent_limit"
            ],
            5,
        )
        self.assertEqual(
            mcp.tools["douyin_browser_list_videos"](10, 5)["offset"],
            5,
        )
        for tool in mcp.tools.values():
            self.assertNotIn("account_id", inspect.signature(tool).parameters)


if __name__ == "__main__":
    unittest.main()
