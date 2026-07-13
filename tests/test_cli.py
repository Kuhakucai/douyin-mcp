import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from douyin_creator_mcp import cli
from douyin_creator_mcp.config import Settings, ensure_runtime_dirs
from douyin_creator_mcp.services.browser_service import BrowserService
from douyin_creator_mcp.storage.db import Database


def make_service(tmp: str) -> BrowserService:
    settings = Settings(
        data_dir=Path(tmp) / "data",
        douyin_browser_profile_dir=Path(tmp) / "data" / "profile",
    )
    ensure_runtime_dirs(settings)
    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    return BrowserService(settings, db)


class CliTests(unittest.TestCase):
    def test_init_returns_absolute_paths_and_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = cli.run_command(argparse.Namespace(command="init"), make_service(tmp))

            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["data_dir"]).is_absolute())
            self.assertEqual(result["schema_version"], "browser-v1")
            self.assertIn("douyin-creator", result["mcp_config"]["mcpServers"])

    def test_purge_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = make_service(tmp)
            result = cli.run_command(
                argparse.Namespace(command="purge", yes=False), service
            )

            self.assertFalse(result["ok"])
            self.assertTrue(service.db.path.exists())

    def test_confirmed_purge_deletes_database_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = make_service(tmp)
            marker = service.settings.douyin_browser_profile_dir / "marker.txt"
            marker.write_text("profile", encoding="utf-8")

            result = cli.run_command(
                argparse.Namespace(command="purge", yes=True), service
            )

            self.assertTrue(result["ok"])
            self.assertFalse(service.db.path.exists())
            self.assertFalse(service.settings.douyin_browser_profile_dir.exists())

    def test_login_success_is_wrapped_as_success_response(self) -> None:
        service = MagicMock()
        service.login_start.return_value = {
            "login_status": "logged_in",
            "title": "抖音创作者中心",
        }

        result = cli._login(service, 1.0, 0.1, lambda _: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        service.close_browser.assert_called_once_with()

    def test_console_streams_are_reconfigured_to_utf8(self) -> None:
        stdout = MagicMock()
        stderr = MagicMock()

        with patch.object(cli.sys, "stdout", stdout), patch.object(
            cli.sys, "stderr", stderr
        ):
            cli._configure_utf8_console()

        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    unittest.main()
