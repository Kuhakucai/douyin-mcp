"""Strict empty/current/verified-legacy migration runner."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..errors import (
    CURRENT_SCHEMA_INVALID,
    LEGACY_SCHEMA_UNRECOGNIZED,
    MIGRATION_CHECKSUM_MISMATCH,
    AppError,
)
from .db import Database


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


REQUIRED_LEGACY_COLUMNS = {
    "videos": {
        "id", "account_id", "title", "publish_time", "duration", "status",
        "source_fingerprint", "parser_version", "first_seen_at", "last_seen_at",
        "is_active", "source", "created_at", "updated_at",
    },
    "video_metrics": {"id", "video_id", "account_id", "metric_date", "source", "created_at"},
    "sync_jobs": {
        "id", "job_type", "status", "started_at", "progress_json", "coverage_json",
        "resume_cursor", "parser_version",
    },
    "video_metric_snapshots": {
        "id", "sync_job_id", "video_id", "account_id", "source", "captured_at",
        "quality", "parser_version", "created_at",
    },
    "video_derived_metrics": {"id", "snapshot_id", "formula_version", "calculated_at"},
    "reports": {"id", "account_id", "period", "created_at"},
    "browser_snapshots": {"id", "account_id", "source_url", "status", "created_at"},
    "browser_account_bindings": {
        "account_id", "fingerprint_salt", "anchor_hashes_json", "anchor_count",
        "created_at", "last_verified_at",
    },
}

MIGRATION_COLUMNS = {
    1: REQUIRED_LEGACY_COLUMNS,
    2: {
        "video_transcript_runs": {
            "id", "account_id", "lifecycle_state", "result", "created_at", "updated_at",
        },
        "video_content_jobs": {
            "id", "video_id", "account_id", "status", "stage", "lease_token",
            "force_requested", "created_at", "updated_at",
        },
        "video_transcript_run_items": {
            "run_id", "job_id", "video_id", "demand_state", "outcome", "attached_at",
        },
        "video_media_assets": {
            "id", "job_id", "video_id", "media_role", "storage_path", "sha256",
        },
        "video_transcripts": {
            "id", "video_id", "job_id", "revision", "is_current", "status", "raw_text",
        },
        "video_transcript_segments": {
            "id", "transcript_id", "segment_index", "start_ms", "end_ms", "text",
        },
    },
    3: {
        "videos": {"visibility", "content_kind", "classification_source"},
    },
}

MIGRATION_INDEXES = {
    1: {
        "idx_reports_account", "idx_sync_jobs_account", "idx_browser_snapshots_account",
        "idx_videos_account", "idx_video_metrics_account",
        "idx_metric_snapshots_video_source_time", "idx_metric_snapshots_job",
        "idx_metric_snapshots_account_time", "idx_derived_snapshot",
    },
    2: {
        "uq_active_video_content_job", "idx_run_items_job", "idx_content_jobs_claim",
        "uq_job_transcription_asset", "uq_video_current_transcript",
        "idx_transcript_segments_page",
    },
}


class MigrationRunner:
    def __init__(
        self,
        db: Database,
        migration_dir: Path | None = None,
        *,
        fail_after_version: int | None = None,
    ):
        self.db = db
        self.migration_dir = migration_dir or Path(__file__).with_name("migrations")
        self.fail_after_version = fail_after_version
        self.backup_path: Path | None = None
        self.migrations = self._load_migrations()

    def apply(self) -> None:
        self.db.path.parent.mkdir(parents=True, exist_ok=True)
        self.db.integrity_check()
        with closing(self.db.connect()) as conn:
            kind = self._classify(conn)
        if kind == "legacy":
            self.backup_path = self._backup()
            self._adopt_legacy()
        elif kind == "unknown":
            raise AppError(
                LEGACY_SCHEMA_UNRECOGNIZED,
                "Database is neither empty nor a verified browser-v1/current schema.",
            )
        elif kind == "empty":
            with closing(self.db.connect()) as conn:
                conn.execute(
                    "CREATE TABLE schema_migrations("
                    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                    "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                conn.commit()
        self._verify_and_apply_pending()

    def _load_migrations(self) -> list[Migration]:
        migrations: list[Migration] = []
        for path in sorted(self.migration_dir.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:3])
            name = path.stem[4:]
            sql = path.read_text(encoding="utf-8")
            migrations.append(
                Migration(
                    version,
                    name,
                    path,
                    sql,
                    hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                )
            )
        if [item.version for item in migrations] != list(range(1, len(migrations) + 1)):
            raise RuntimeError("Migration versions must be contiguous from 001.")
        return migrations

    @staticmethod
    def _user_tables(conn: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _classify(self, conn: sqlite3.Connection) -> str:
        tables = self._user_tables(conn)
        if not tables:
            return "empty"
        if "schema_migrations" in tables:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(schema_migrations)")
            }
            if {"version", "name", "checksum", "applied_at"} <= columns:
                count = int(
                    conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                )
                if count == 0:
                    return "current" if tables == {"schema_migrations"} else "unknown"
                return "current"
            if columns == {"version", "applied_at"} and self._legacy_tables_valid(conn):
                versions = {
                    str(row[0])
                    for row in conn.execute("SELECT version FROM schema_migrations")
                }
                return "legacy" if not versions or "browser-v1" in versions else "unknown"
            return "unknown"
        return "legacy" if self._legacy_tables_valid(conn) else "unknown"

    @staticmethod
    def _legacy_tables_valid(conn: sqlite3.Connection) -> bool:
        tables = MigrationRunner._user_tables(conn)
        if not REQUIRED_LEGACY_COLUMNS.keys() <= tables:
            return False
        for table, required in REQUIRED_LEGACY_COLUMNS.items():
            columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
            if not required <= columns:
                return False
        return True

    def _backup(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        target = self.db.path.with_name(f"{self.db.path.name}.backup-{stamp}")
        with closing(sqlite3.connect(self.db.path)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
        return target

    def _adopt_legacy(self) -> None:
        baseline = self.migrations[0]
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                tables = self._user_tables(conn)
                if "schema_migrations" in tables:
                    conn.execute("ALTER TABLE schema_migrations RENAME TO schema_migrations_legacy")
                conn.execute(
                    "CREATE TABLE schema_migrations("
                    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                    "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) "
                    "VALUES(?,?,?,?)",
                    (baseline.version, baseline.name, baseline.checksum, self._now()),
                )
                if "schema_migrations_legacy" in self._user_tables(conn):
                    conn.execute("DROP TABLE schema_migrations_legacy")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _verify_and_apply_pending(self) -> None:
        known = {item.version: item for item in self.migrations}
        with closing(self.db.connect()) as conn:
            applied = conn.execute(
                "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            for row in applied:
                migration = known.get(int(row["version"]))
                if (
                    migration is None
                    or row["name"] != migration.name
                    or row["checksum"] != migration.checksum
                ):
                    raise AppError(
                        MIGRATION_CHECKSUM_MISMATCH,
                        f"Applied migration {row['version']} does not match this release.",
                    )
            applied_versions = {int(row["version"]) for row in applied}
            self._verify_schema(conn, applied_versions)
        for migration in self.migrations:
            if migration.version in applied_versions:
                continue
            self._apply_one(migration)

    @staticmethod
    def _verify_schema(
        conn: sqlite3.Connection, applied_versions: set[int]
    ) -> None:
        missing: list[str] = []
        for version in sorted(applied_versions):
            for table, required in MIGRATION_COLUMNS.get(version, {}).items():
                columns = {
                    str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
                }
                for column in sorted(required - columns):
                    missing.append(f"{table}.{column}")
            indexes = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            missing.extend(
                f"index:{name}"
                for name in sorted(MIGRATION_INDEXES.get(version, set()) - indexes)
            )
        if missing:
            raise AppError(
                CURRENT_SCHEMA_INVALID,
                "Applied migration ledger does not match the database schema.",
                extra={"missing_schema_objects": missing},
            )

    def _apply_one(self, migration: Migration) -> None:
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in self._statements(migration.sql):
                    conn.execute(statement)
                if self.fail_after_version == migration.version:
                    raise RuntimeError(f"fault injection after migration {migration.version}")
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) "
                    "VALUES(?,?,?,?)",
                    (migration.version, migration.name, migration.checksum, self._now()),
                )
                conn.commit()
                self._verify_schema(conn, {migration.version})
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _statements(sql: str) -> list[str]:
        statements: list[str] = []
        buffer = ""
        for line in sql.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                if statement:
                    statements.append(statement)
                buffer = ""
        if buffer.strip():
            raise sqlite3.OperationalError("Incomplete migration statement.")
        return statements

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
