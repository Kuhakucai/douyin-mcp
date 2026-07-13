import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from douyin_creator_mcp.storage.db import Database, SCHEMA_VERSION


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_database_is_backed_up_and_migrated_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "douyin.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                with conn:
                    conn.executescript(
                    """
                    CREATE TABLE videos (
                      id TEXT PRIMARY KEY, account_id TEXT NOT NULL, item_id TEXT,
                      video_id TEXT, title TEXT, publish_time INTEGER, cover_url TEXT,
                      video_url TEXT, duration INTEGER, source TEXT NOT NULL,
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE sync_jobs (
                      id TEXT PRIMARY KEY, account_id TEXT, job_type TEXT NOT NULL,
                      status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                      error_type TEXT, error_message TEXT
                    );
                    INSERT INTO videos VALUES (
                      'v1', 'browser-default', NULL, NULL, 'legacy', 1, NULL,
                      NULL, 10, 'browser_dom', 't', 't'
                    );
                    """
                    )

            db = Database(path)
            backup = db.init_schema()

            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
            self.assertEqual(db.query_one("SELECT title FROM videos WHERE id='v1'")["title"], "legacy")
            columns = {row["name"] for row in db.query_all("PRAGMA table_info(videos)")}
            self.assertIn("source_fingerprint", columns)
            self.assertIsNone(db.init_schema())

    def test_failed_migration_restores_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "douyin.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                with conn:
                    conn.executescript(
                        "CREATE TABLE legacy (id TEXT PRIMARY KEY, value TEXT);"
                        "INSERT INTO legacy VALUES ('1', 'kept');"
                    )
            bad_schema = Path(tmp) / "bad.sql"
            bad_schema.write_text(
                "CREATE TABLE temporary_change (id TEXT); INVALID SQL;",
                encoding="utf-8",
            )
            db = Database(path)

            with self.assertRaises(sqlite3.OperationalError):
                db.init_schema(bad_schema)

            self.assertEqual(
                db.query_one("SELECT value FROM legacy WHERE id='1'")["value"], "kept"
            )
            self.assertIsNone(
                db.query_one(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='temporary_change'"
                )
            )


if __name__ == "__main__":
    unittest.main()
