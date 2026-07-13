"""Minimal cross-process lock for the single persistent browser profile."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..errors import PROFILE_IN_USE, AppError


class ProfileLock:
    def __init__(self, profile_dir: Path, filename: str = ".douyin-mcp.lock") -> None:
        self.path = profile_dir / filename
        self.owner = str(uuid.uuid4())
        self._acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "owner": self.owner,
                "pid": os.getpid(),
                "acquired_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
        ).encode("utf-8")
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AppError(
                PROFILE_IN_USE,
                "The dedicated browser profile is already being used by another sync.",
                retryable=True,
            ) from exc
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        if current.get("owner") == self.owner:
            self.path.unlink(missing_ok=True)
        self._acquired = False

    def __enter__(self) -> ProfileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def inspect(self) -> dict[str, object]:
        if not self.path.exists():
            return {"locked": False, "path": str(self.path)}
        try:
            detail = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            detail = {}
        return {"locked": True, "path": str(self.path), **detail}
