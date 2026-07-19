"""Read-only adapter for the v1.2 real-account ASR validation database."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class BatchValidationSummary:
    parent_count: int
    analysis_ready_count: int
    no_speech_count: int
    segment_count: int
    timeline_valid: bool
    text_hash_valid: bool
    url_hit_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_count": self.parent_count,
            "analysis_ready_count": self.analysis_ready_count,
            "no_speech_count": self.no_speech_count,
            "segment_count": self.segment_count,
            "timeline_valid": self.timeline_valid,
            "text_hash_valid": self.text_hash_valid,
            "url_hit_count": self.url_hit_count,
        }


class BatchTranscriptCompatibilityReader:
    REQUIRED_TABLES = {"batch_items", "transcripts", "transcript_segments"}

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def validate(self) -> BatchValidationSummary:
        with closing(self.connect()) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = self.REQUIRED_TABLES - tables
            if missing:
                raise ValueError(f"Batch database is missing tables: {sorted(missing)}")
            states = {
                str(row["state"]): int(row["count"])
                for row in conn.execute(
                    "SELECT state,COUNT(*) AS count FROM batch_items GROUP BY state"
                )
            }
            parent_count = int(
                conn.execute("SELECT COUNT(*) FROM batch_items").fetchone()[0]
            )
            segment_count = int(
                conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
            )
            timeline_valid, text_hash_valid = self._validate_transcripts(conn)
            url_hits = self._count_urls(conn)
        return BatchValidationSummary(
            parent_count,
            states.get("transcript_ready", 0),
            states.get("no_speech", 0),
            segment_count,
            timeline_valid,
            text_hash_valid,
            url_hits,
        )

    def iter_content_packages(self) -> Iterator[dict[str, Any]]:
        with closing(self.connect()) as conn:
            transcripts = conn.execute(
                "SELECT i.item_key,i.platform_video_id,i.declared_duration_seconds,"
                "i.state,t.status,t.provider,t.model,t.language,t.raw_text,t.text_sha256 "
                "FROM batch_items i LEFT JOIN transcripts t ON t.item_key=i.item_key "
                "ORDER BY i.publish_time,i.item_key"
            ).fetchall()
            for row in transcripts:
                segments = [
                    dict(item)
                    for item in conn.execute(
                        "SELECT segment_index,start_ms,end_ms,text,avg_logprob,no_speech_prob "
                        "FROM transcript_segments WHERE item_key=? ORDER BY segment_index",
                        (row["item_key"],),
                    )
                ]
                yield {
                    "content_id": row["item_key"],
                    "platform_video_id": row["platform_video_id"],
                    "duration_ms": (
                        int(row["declared_duration_seconds"]) * 1000
                        if row["declared_duration_seconds"] is not None
                        else None
                    ),
                    "readiness": (
                        "analysis_ready"
                        if row["state"] == "transcript_ready"
                        else row["state"]
                    ),
                    "provider": row["provider"],
                    "model": row["model"],
                    "language": row["language"],
                    "text_sha256": row["text_sha256"],
                    "segment_count": len(segments),
                    "segments": segments,
                    "trust": "untrusted_content",
                    "source": "asr",
                }

    @staticmethod
    def _validate_transcripts(conn: sqlite3.Connection) -> tuple[bool, bool]:
        timeline_valid = True
        text_hash_valid = True
        rows = conn.execute(
            "SELECT item_key,status,raw_text,text_sha256 FROM transcripts"
        ).fetchall()
        for transcript in rows:
            segments = conn.execute(
                "SELECT segment_index,start_ms,end_ms,text FROM transcript_segments "
                "WHERE item_key=? ORDER BY segment_index",
                (transcript["item_key"],),
            ).fetchall()
            for expected, segment in enumerate(segments):
                if (
                    int(segment["segment_index"]) != expected
                    or int(segment["start_ms"]) < 0
                    or int(segment["end_ms"]) < int(segment["start_ms"])
                ):
                    timeline_valid = False
            raw_text = str(transcript["raw_text"] or "")
            if hashlib.sha256(raw_text.encode("utf-8")).hexdigest() != transcript["text_sha256"]:
                text_hash_valid = False
            if transcript["status"] == "transcript_ready":
                joined = "".join(str(segment["text"]) for segment in segments)
                if joined != raw_text:
                    text_hash_valid = False
        return timeline_valid, text_hash_valid

    @staticmethod
    def _count_urls(conn: sqlite3.Connection) -> int:
        hits = 0
        pattern = re.compile(r"https?://", re.IGNORECASE)
        for table in ("batch_items", "transcripts", "transcript_segments"):
            columns = [
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})")
                if str(row["type"]).upper() == "TEXT"
            ]
            for column in columns:
                for row in conn.execute(
                    f"SELECT {column} AS value FROM {table} WHERE {column} IS NOT NULL"
                ):
                    if pattern.search(str(row["value"])):
                        hits += 1
        return hits
