from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.api_mapping import load_api_mapping
from douyin_creator_mcp.config import Settings, generate_token_key
from douyin_creator_mcp.services.douyin_api import DouyinApiClient


class ApiMappingTests(unittest.TestCase):
    def test_loads_mapping_from_docs(self):
        mapping = load_api_mapping(ROOT / "docs" / "api-mapping.md")
        self.assertIn("user_info", mapping.capability_items())

    def test_build_request_injects_query_auth(self):
        settings = Settings(token_encryption_key=generate_token_key())
        mapping = load_api_mapping(ROOT / "docs" / "api-mapping.md")
        client = DouyinApiClient(settings, mapping)

        request = client.build_request("get_user_info", access_token="access", open_id="open")

        self.assertEqual(request.method, "GET")
        self.assertEqual(request.params["access_token"], "access")
        self.assertEqual(request.params["open_id"], "open")

    def test_build_request_injects_header_auth(self):
        settings = Settings(token_encryption_key=generate_token_key())
        mapping = load_api_mapping(ROOT / "docs" / "api-mapping.md")
        client = DouyinApiClient(settings, mapping)

        request = client.build_request("fans_data", access_token="access", open_id="open")

        self.assertEqual(request.headers["access-token"], "access")
        self.assertEqual(request.params["open_id"], "open")


if __name__ == "__main__":
    unittest.main()
