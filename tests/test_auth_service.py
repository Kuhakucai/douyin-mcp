from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.config import Settings, generate_token_key
from douyin_creator_mcp.server import build_container


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "data": {
                "open_id": "open-1",
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "scope": "user_info",
                "nickname": "creator",
            }
        }


class FakeHttpClient:
    def __init__(self):
        self.requests = []

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return FakeResponse()


class AuthServiceTests(unittest.TestCase):
    def test_auth_start_and_complete_do_not_expose_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                data_dir=Path(tmp),
                douyin_client_key="client-key",
                douyin_client_secret="client-secret",
                token_encryption_key=generate_token_key(),
                api_mapping_file=ROOT / "docs" / "api-mapping.md",
            )
            http_client = FakeHttpClient()
            container = build_container(settings, http_client=http_client)

            started = container.auth_service.start_auth()
            completed = container.auth_service.complete_auth("oauth-code", started["state"])
            status = container.auth_service.get_auth_status(started["auth_session_id"])

            self.assertEqual(completed["account"]["id"], "open-1")
            self.assertEqual(status["auth_session"]["status"], "completed")
            self.assertNotIn("access_token", str(completed))
            self.assertNotIn("refresh_token", str(status))
            self.assertIn("code", http_client.requests[0][1]["data"])


if __name__ == "__main__":
    unittest.main()
