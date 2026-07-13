"""Small SQLite wrapper used by the service layer."""

from __future__ import annotations

import sqlite3
import shutil
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = "browser-v1"

_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "videos": {
        "status": "TEXT",
        "source_fingerprint": "TEXT",
        "parser_version": "TEXT",
        "first_seen_at": "TEXT",
        "last_seen_at": "TEXT",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    },
    "sync_jobs": {
        "progress_json": "TEXT",
        "coverage_json": "TEXT",
        "resume_cursor": "INTEGER",
        "parser_version": "TEXT",
    },
}


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.last_backup_path: Path | None = None

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init_schema(self, schema_path: Path | str | None = None) -> Path | None:
        self.last_backup_path = None
        path = Path(schema_path) if schema_path else Path(__file__).with_name("schemas.sql")
        sql = path.read_text(encoding="utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._needs_v1_backup():
            self.last_backup_path = self._backup_database()
        try:
            with closing(self.connect()) as conn:
                with conn:
                    conn.executescript(sql)
                    self._ensure_added_columns(conn)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                        VALUES (?, ?)
                        """,
                        (SCHEMA_VERSION, self._utc_now_iso()),
                    )
        except Exception:
            if self.last_backup_path is not None and self.last_backup_path.exists():
                shutil.copy2(self.last_backup_path, self.path)
            raise
        return self.last_backup_path

    def schema_version(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            row = self.query_one(
                "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
            )
        except sqlite3.OperationalError:
            return None
        return str(row["version"]) if row else None

    def _needs_v1_backup(self) -> bool:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with closing(sqlite3.connect(self.path)) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                return False
            if "schema_migrations" not in tables:
                return True
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ? LIMIT 1",
                (SCHEMA_VERSION,),
            ).fetchone()
            return row is None

    def _backup_database(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        backup_path = self.path.with_name(f"{self.path.name}.backup-{stamp}")
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
        return backup_path

    @staticmethod
    def _ensure_added_columns(conn: sqlite3.Connection) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with closing(self.connect()) as conn:
            with conn:
                conn.execute(sql, params)

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
