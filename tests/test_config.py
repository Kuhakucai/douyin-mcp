from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.config import (
    Settings,
    ensure_runtime_dirs,
    load_settings,
    validate_for_http,
)
from douyin_creator_mcp.errors import AppError


class ConfigTests(unittest.TestCase):
    def test_load_settings_from_env_mapping(self):
        settings = load_settings(
            {
                "MCP_TRANSPORT": "http",
                "MCP_PORT": "9000",
                "DOUYIN_BROWSER_PAGE_SETTLE_MS": "2500",
            },
            dotenv_path="missing.env",
        )

        self.assertEqual(settings.mcp_transport, "http")
        self.assertEqual(settings.mcp_port, 9000)
        self.assertEqual(settings.douyin_browser_page_settle_ms, 2500)

    def test_runtime_dirs_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(data_dir=Path(tmp))
            ensure_runtime_dirs(settings)
            self.assertTrue((Path(tmp) / "reports").exists())
            self.assertTrue((Path(tmp) / "logs").exists())

    def test_http_mode_requires_api_key(self):
        settings = Settings(mcp_transport="http")
        with self.assertRaises(AppError):
            validate_for_http(settings)


if __name__ == "__main__":
    unittest.main()
