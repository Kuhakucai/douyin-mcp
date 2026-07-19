"""SQLite connections with production PRAGMAs and ordered migrations."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.last_backup_path: Path | None = None

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def init_schema(self, schema_path: Path | str | None = None) -> Path | None:
        """Install or migrate the database.

        ``schema_path`` is kept only for the legacy public API. Custom schema replay is
        deliberately rejected because published migrations are immutable.
        """
        if schema_path is not None:
            raise ValueError("Custom schema replay is unsupported; use MigrationRunner.")
        from .migration import MigrationRunner

        runner = MigrationRunner(self)
        runner.apply()
        self.last_backup_path = runner.backup_path
        return self.last_backup_path

    def schema_version(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            with closing(self.connect(read_only=True)) as conn:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(schema_migrations)")
                }
                if "name" not in columns:
                    row = conn.execute(
                        "SELECT version FROM schema_migrations "
                        "ORDER BY applied_at DESC LIMIT 1"
                    ).fetchone()
                    return str(row["version"]) if row else None
                row = conn.execute(
                    "SELECT version, name FROM schema_migrations "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        return f"{row['version']}:{row['name']}" if row else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with closing(self.connect()) as conn:
            with conn:
                conn.execute(sql, params)

    def query_one(
        self, sql: str, params: tuple[Any, ...] = (), *, read_only: bool = False
    ) -> dict[str, Any] | None:
        with closing(self.connect(read_only=read_only)) as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def query_all(
        self, sql: str, params: tuple[Any, ...] = (), *, read_only: bool = False
    ) -> list[dict[str, Any]]:
        with closing(self.connect(read_only=read_only)) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def integrity_check(self) -> None:
        if not self.path.exists():
            return
        with closing(self.connect(read_only=True)) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError("SQLite integrity_check failed.")

    def checkpoint(self) -> None:
        if not self.path.exists():
            return
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
