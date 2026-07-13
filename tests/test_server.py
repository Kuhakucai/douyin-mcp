from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from douyin_creator_mcp.config import Settings, generate_token_key
from douyin_creator_mcp import server


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator


class ServerTests(unittest.TestCase):
    def test_browser_container_does_not_require_openapi_or_token_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                data_dir=Path(tmp),
                douyin_browser_profile_dir=Path(tmp) / "profile",
                token_encryption_key="",
            )
            container = server.build_browser_container(settings)

            self.assertEqual(container.db.schema_version(), "browser-v1")
            self.assertEqual(container.settings.token_encryption_key, "")

    def test_default_mcp_only_registers_browser_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            services = server.build_browser_container(
                Settings(
                    data_dir=Path(tmp),
                    douyin_browser_profile_dir=Path(tmp) / "profile",
                )
            )
            fake = FakeMCP()
            with patch.object(server, "_new_mcp", return_value=fake):
                result = server.create_mcp(services)

            self.assertIs(result, fake)
            self.assertTrue(result.tools)
            self.assertTrue(all(name.startswith("douyin_browser_") for name in result.tools))
            self.assertFalse(any("auth" in name or "account" in name for name in result.tools))

    def test_legacy_container_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                data_dir=Path(tmp),
                douyin_browser_profile_dir=Path(tmp) / "profile",
                token_encryption_key=generate_token_key(),
                api_mapping_file=Path("docs/api-mapping.md"),
            )
            self.assertIsNotNone(server.build_container(settings))


if __name__ == "__main__":
    unittest.main()
