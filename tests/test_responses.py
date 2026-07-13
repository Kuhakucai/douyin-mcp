from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.responses import REDACTED, sanitize_payload, success_response


class ResponseTests(unittest.TestCase):
    def test_sensitive_fields_are_redacted_recursively(self):
        payload = {
            "access_token": "plain-access",
            "nested": {
                "refreshToken": "plain-refresh",
                "safe": "value",
                "items": [{"client_secret": "secret"}],
            },
        }

        result = sanitize_payload(payload)

        self.assertEqual(result["access_token"], REDACTED)
        self.assertEqual(result["nested"]["refreshToken"], REDACTED)
        self.assertEqual(result["nested"]["items"][0]["client_secret"], REDACTED)
        self.assertEqual(result["nested"]["safe"], "value")

    def test_success_response_sanitizes_payload(self):
        result = success_response(code="oauth-code", account={"id": "1"})
        self.assertEqual(result["code"], REDACTED)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
