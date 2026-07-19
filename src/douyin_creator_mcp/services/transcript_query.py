"""Immutable-revision transcript pagination and deterministic AI context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from ..errors import (
    DATA_NOT_AVAILABLE,
    INVALID_CURSOR,
    TRANSCRIPT_REVISION_GONE,
    VALIDATION_ERROR,
    AppError,
)
from ..storage.db import Database


class CursorSigner:
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("Cursor HMAC key must contain at least 32 bytes.")
        self.key = key

    @classmethod
    def load_or_create(cls, path: Path) -> CursorSigner:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = path.open("xb")
        except FileExistsError:
            key = path.read_bytes()
        else:
            with descriptor:
                import os

                key = os.urandom(32)
                descriptor.write(key)
                descriptor.flush()
                os.fsync(descriptor.fileno())
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return cls(key)

    def encode(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self.key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode("ascii")

    def decode(self, cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = base64.urlsafe_b64decode(padded)
            raw, signature = value[:-32], value[-32:]
            expected = hmac.new(self.key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError("version")
            return payload
        except Exception as exc:
            raise AppError(INVALID_CURSOR, "Transcript cursor is invalid.") from exc


class TranscriptQueryService:
    def __init__(
        self,
        db: Database,
        signer: CursorSigner,
        *,
        response_max_bytes: int = 262_144,
    ):
        self.db = db
        self.signer = signer
        self.response_max_bytes = min(response_max_bytes, 262_144)

    def get_video_transcript(
        self,
        video_id: str,
        *,
        revision: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
        include_raw_text: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise AppError(VALIDATION_ERROR, "limit must be between 1 and 100.")
        query_hash = self._query_hash(
            {
                "video_id": video_id,
                "revision": revision,
                "limit": limit,
                "include_raw_text": include_raw_text,
            }
        )
        start_index = 0
        transcript_id: str | None = None
        fixed_revision = revision
        if cursor:
            payload = self.signer.decode(cursor)
            if payload.get("video_id") != video_id or payload.get("query_hash") != query_hash:
                raise AppError(INVALID_CURSOR, "Cursor does not match query parameters.")
            transcript_id = str(payload["transcript_id"])
            fixed_revision = int(payload["revision"])
            start_index = int(payload["last_segment_index"]) + 1
        if transcript_id:
            transcript = self.db.query_one(
                "SELECT t.*,v.title,v.duration AS video_duration FROM video_transcripts t "
                "JOIN videos v ON v.id=t.video_id WHERE t.id=? AND t.video_id=?",
                (transcript_id, video_id),
                read_only=True,
            )
            if transcript is None:
                raise AppError(
                    TRANSCRIPT_REVISION_GONE,
                    "The transcript revision captured by this cursor no longer exists.",
                )
        else:
            where = "t.video_id=? AND t.is_current=1"
            params: tuple[Any, ...] = (video_id,)
            if fixed_revision is not None:
                where = "t.video_id=? AND t.revision=?"
                params = (video_id, fixed_revision)
            transcript = self.db.query_one(
                "SELECT t.*,v.title,v.duration AS video_duration FROM video_transcripts t "
                f"JOIN videos v ON v.id=t.video_id WHERE {where}",
                params,
                read_only=True,
            )
            if transcript is None:
                raise AppError(DATA_NOT_AVAILABLE, "Transcript revision is not available.")
            transcript_id = str(transcript["id"])
            fixed_revision = int(transcript["revision"])
        rows = self.db.query_all(
            "SELECT segment_index,start_ms,end_ms,text,avg_logprob,no_speech_prob,language "
            "FROM video_transcript_segments WHERE transcript_id=? AND segment_index>=? "
            "ORDER BY segment_index LIMIT ?",
            (transcript_id, start_index, limit + 1),
            read_only=True,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            next_cursor = self.signer.encode(
                {
                    "v": 1,
                    "video_id": video_id,
                    "transcript_id": transcript_id,
                    "revision": fixed_revision,
                    "last_segment_index": rows[-1]["segment_index"],
                    "query_hash": query_hash,
                }
            )
        result = {
            "video_id": video_id,
            "title": transcript["title"],
            "duration_ms": transcript["duration_ms"]
            or (
                int(transcript["video_duration"]) * 1000
                if transcript["video_duration"] is not None
                else None
            ),
            "transcript_id": transcript_id,
            "revision": fixed_revision,
            "readiness": (
                "analysis_ready"
                if transcript["status"] == "available"
                else "no_speech"
            ),
            "text_sha256": transcript["text_sha256"],
            "provider": transcript["provider"],
            "model": transcript["model"],
            "model_version": transcript["model_version"],
            "extractor_version": transcript["extractor_version"],
            "language": transcript["language"],
            "segment_count": transcript["segment_count"],
            "segments": rows,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "warnings": [],
            "trust": "untrusted_content",
            "source": "asr",
        }
        if include_raw_text:
            result["raw_text"] = transcript["raw_text"]
        encoded = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > self.response_max_bytes:
            raise AppError(
                VALIDATION_ERROR,
                "Requested transcript page exceeds the configured response limit; reduce limit.",
            )
        return result

    def get_video_analysis_context(
        self,
        video_id: str,
        *,
        revision: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        if not 1_000 <= max_chars <= 100_000:
            raise AppError(VALIDATION_ERROR, "max_chars must be between 1000 and 100000.")
        page = self.get_video_transcript(
            video_id,
            revision=revision,
            limit=limit,
            cursor=cursor,
            include_raw_text=False,
        )
        paragraphs: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        consumed: list[dict[str, Any]] = []
        chars = 0
        truncated = False
        for position, segment in enumerate(page["segments"]):
            text = str(segment["text"])
            if chars + len(text) > max_chars:
                if not consumed:
                    current.append(segment)
                    consumed.append(segment)
                    chars += len(text)
                    page["warnings"].append(
                        "budget_exceeded: one complete segment exceeds max_chars "
                        "and was returned intact."
                    )
                    truncated = (
                        position < len(page["segments"]) - 1 or page["has_more"]
                    )
                else:
                    truncated = True
                break
            split = bool(
                current
                and (
                    int(segment["start_ms"]) - int(current[-1]["end_ms"]) >= 1200
                    or sum(len(str(item["text"])) for item in current) >= 800
                )
            )
            if split:
                paragraphs.append(self._paragraph(current))
                current = []
            current.append(segment)
            consumed.append(segment)
            chars += len(text)
        if current:
            paragraphs.append(self._paragraph(current))
        page["paragraphs"] = paragraphs
        page["context_chars"] = chars
        page["context_truncated"] = truncated
        if truncated and page["segments"]:
            first_index = int(page["segments"][0]["segment_index"])
            last_index = (
                int(consumed[-1]["segment_index"]) if consumed else first_index - 1
            )
            page["has_more"] = True
            page["next_cursor"] = self.signer.encode(
                {
                    "v": 1,
                    "video_id": video_id,
                    "transcript_id": page["transcript_id"],
                    "revision": page["revision"],
                    "last_segment_index": last_index,
                    "query_hash": self._query_hash(
                        {
                            "video_id": video_id,
                            "revision": revision,
                            "limit": limit,
                            "include_raw_text": False,
                        }
                    ),
                }
            )
        page.pop("segments", None)
        return page

    @staticmethod
    def _paragraph(segments: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "start_ms": segments[0]["start_ms"],
            "end_ms": segments[-1]["end_ms"],
            "segment_start_index": segments[0]["segment_index"],
            "segment_end_index": segments[-1]["segment_index"],
            "text": "".join(str(item["text"]) for item in segments),
        }

    @staticmethod
    def _query_hash(value: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
