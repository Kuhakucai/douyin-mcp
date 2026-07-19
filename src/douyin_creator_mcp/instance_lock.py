"""Process-wide DATA_DIR lock used before database migration or worker startup."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from .errors import INSTANCE_IN_USE, AppError


class InstanceLock:
    """Non-blocking operating-system lock; metadata is diagnostic only."""

    def __init__(self, data_dir: Path | str, filename: str = ".douyin-mcp.instance.lock"):
        self.path = Path(data_dir) / filename
        self._handle: IO[bytes] | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            self._lock(handle)
        except OSError as exc:
            handle.close()
            raise AppError(
                INSTANCE_IN_USE,
                "Another douyin-mcp process owns this DATA_DIR.",
                retryable=True,
            ) from exc
        metadata = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, sort_keys=True).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    @staticmethod
    def _lock(handle: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
