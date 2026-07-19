"""Schema-limited structured events; arbitrary content is rejected."""

from __future__ import annotations

import json
import logging
from typing import Any

from .responses import sanitize_payload

ALLOWED_FIELDS = {
    "event",
    "run_id",
    "job_id",
    "video_id",
    "stage",
    "error_type",
    "count",
    "duration_ms",
    "pipeline_version",
}


def structured_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    unknown = set(fields) - (ALLOWED_FIELDS - {"event"})
    if unknown:
        raise ValueError(f"Unsupported structured log fields: {sorted(unknown)}")
    payload = sanitize_payload({"event": event, **fields})
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
