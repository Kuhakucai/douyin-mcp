import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from douyin_creator_mcp import browser_smoke


class FakeBrowserService:
    def __init__(self, login_results=None) -> None:
        self.login_results = list(login_results or [])
        self.close_count = 0
        self.calls = []

    def login_start(self):
        self.calls.append(("login_start",))
        return self.login_results.pop(0)

    def login_status(self):
        self.calls.append(("login_status",))
        return self.login_results.pop(0)

    def close_browser(self):
        self.close_count += 1

    def sync_creator_data(self, **kwargs):
        self.calls.append(("sync", kwargs))
        return {"status": "completed", "snapshot_id": "snapshot-1"}

    def sync_video_details(self, **kwargs):
        self.calls.append(("details", kwargs))
        return {"status": "completed", "job_id": "job-1"}

    def refresh_report(self, **kwargs):
        self.calls.append(("report", kwargs))
        return {"report_id": "report-1"}

    def latest_snapshot_summary(self):
        self.calls.append(("latest-snapshot",))
        return {"snapshot_id": "snapshot-1", "video_candidate_count": 2}

    def list_videos(self, **kwargs):
        self.calls.append(("videos", kwargs))
        return {"total": 1, "videos": [{"id": "video-1"}]}


class BrowserSmokeTests(unittest.TestCase):
    def test_login_returns_immediately_when_profile_is_logged_in(self) -> None:
        service = FakeBrowserService([{"login_status": "logged_in"}])
        args = argparse.Namespace(command="login", timeout=10.0, poll_interval=1.0)

        result = browser_smoke.run_command(args, service, lambda _: None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(service.close_count, 1)
        self.assertEqual(service.calls, [("login_start",)])

    def test_login_polls_until_logged_in(self) -> None:
        service = FakeBrowserService(
            [
                {"login_status": "login_required"},
                {"login_status": "verification_required"},
                {"login_status": "logged_in"},
            ]
        )
        args = argparse.Namespace(command="login", timeout=2.0, poll_interval=1.0)

        result = browser_smoke.run_command(args, service, lambda _: None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(service.close_count, 1)
        self.assertEqual(service.calls.count(("login_status",)), 2)

    def test_login_timeout_is_retryable_and_closes_browser(self) -> None:
        service = FakeBrowserService([{"login_status": "login_required"}])
        args = argparse.Namespace(command="login", timeout=0.0, poll_interval=1.0)

        result = browser_smoke.run_command(args, service, lambda _: None)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "login_timeout")
        self.assertTrue(result["retryable"])
        self.assertEqual(service.close_count, 1)

    def test_login_closes_browser_when_start_fails(self) -> None:
        service = FakeBrowserService()
        args = argparse.Namespace(command="login", timeout=1.0, poll_interval=1.0)

        with self.assertRaises(IndexError):
            browser_smoke.run_command(args, service, lambda _: None)

        self.assertEqual(service.close_count, 1)

    def test_status_opens_and_closes_browser(self) -> None:
        service = FakeBrowserService([{"login_status": "logged_in"}])
        args = argparse.Namespace(command="status")

        result = browser_smoke.run_command(args, service)

        self.assertEqual(result["command"], "status")
        self.assertEqual(service.close_count, 1)

    def test_sync_uses_single_account_and_closes_browser(self) -> None:
        service = FakeBrowserService()
        args = argparse.Namespace(command="sync", mode="visible")

        result = browser_smoke.run_command(args, service)

        self.assertEqual(result["result"]["snapshot_id"], "snapshot-1")
        self.assertIn(("sync", {"mode": "visible"}), service.calls)
        self.assertEqual(service.close_count, 1)

    def test_report_and_latest_snapshot_dispatch(self) -> None:
        service = FakeBrowserService()

        report = browser_smoke.run_command(
            argparse.Namespace(command="report", period="latest"),
            service,
        )
        snapshot = browser_smoke.run_command(
            argparse.Namespace(command="latest-snapshot"),
            service,
        )

        self.assertEqual(report["result"]["report_id"], "report-1")
        self.assertEqual(snapshot["result"]["video_candidate_count"], 2)

    def test_details_dispatches_batch_arguments(self) -> None:
        service = FakeBrowserService()
        args = argparse.Namespace(
            command="details",
            video_ids=["v1"],
            recent_limit=20,
            force=True,
            batch_size=5,
            cursor=0,
            mode="visible",
        )

        result = browser_smoke.run_command(args, service)

        self.assertEqual(result["result"]["job_id"], "job-1")
        self.assertEqual(service.calls[0][1]["video_ids"], ["v1"])
        self.assertEqual(service.close_count, 1)

    def test_videos_dispatches_pagination(self) -> None:
        service = FakeBrowserService()

        result = browser_smoke.run_command(
            argparse.Namespace(
                command="videos",
                limit=10,
                offset=5,
            ),
            service,
        )

        self.assertEqual(result["result"]["total"], 1)
        self.assertIn(("videos", {"limit": 10, "offset": 5}), service.calls)

    def test_main_prints_structured_error_and_returns_nonzero(self) -> None:
        output = StringIO()
        with patch.object(browser_smoke, "build_service", side_effect=RuntimeError("boom")):
            with redirect_stdout(output):
                exit_code = browser_smoke.main(["latest-snapshot"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["command"], "latest-snapshot")

    def test_parser_rejects_missing_subcommand(self) -> None:
        parser = browser_smoke.build_parser()
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
