from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.config import generate_token_key
from douyin_creator_mcp.storage.db import Database
from douyin_creator_mcp.storage.token_store import TokenStore


class TokenStoreTests(unittest.TestCase):
    def test_tokens_are_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "douyin.sqlite"
            db = Database(db_path)
            db.init_schema()
            store = TokenStore(db, generate_token_key())

            store.save_tokens("account-1", "access-plain", "refresh-plain", int(time.time()) + 3600)
            record = store.get_tokens("account-1")

            self.assertEqual(record.access_token, "access-plain")
            self.assertEqual(record.refresh_token, "refresh-plain")

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT access_token_encrypted, refresh_token_encrypted FROM tokens WHERE account_id = ?",
                    ("account-1",),
                ).fetchone()
            self.assertNotIn("access-plain", row[0])
            self.assertNotIn("refresh-plain", row[1])

    def test_expired_token_can_be_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "douyin.sqlite")
            db.init_schema()
            store = TokenStore(db, generate_token_key())
            store.save_tokens("account-1", "old-access", "old-refresh", int(time.time()) - 1)

            record = store.get_valid_token(
                "account-1",
                lambda refresh: {
                    "access_token": "new-access",
                    "refresh_token": refresh,
                    "expires_in": 3600,
                },
            )

            self.assertEqual(record.access_token, "new-access")


if __name__ == "__main__":
    unittest.main()
