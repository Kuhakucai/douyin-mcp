"""Minimal cross-process lock for the single persistent browser profile."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import PROFILE_IN_USE, AppError


DEFAULT_STALE_GRACE_SECONDS = 30.0


class ProfileLock:
    def __init__(
        self,
        profile_dir: Path,
        filename: str = ".douyin-mcp.lock",
        stale_grace_seconds: float = DEFAULT_STALE_GRACE_SECONDS,
    ) -> None:
        if stale_grace_seconds < 0:
            raise ValueError("stale_grace_seconds must be non-negative")
        self.path = profile_dir / filename
        self.owner = str(uuid.uuid4())
        self.stale_grace_seconds = stale_grace_seconds
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

        for attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                if attempt == 0 and self._reclaim_stale_lock():
                    continue
                raise AppError(
                    PROFILE_IN_USE,
                    "专用浏览器 profile 正被另一个同步任务使用。",
                    retryable=True,
                ) from exc
        else:  # pragma: no cover - the loop always returns or raises
            raise RuntimeError("profile lock acquisition failed")

        try:
            with os.fdopen(descriptor, "wb") as lock_file:
                lock_file.write(payload)
                lock_file.flush()
                os.fsync(lock_file.fileno())
        except BaseException:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self._acquired = True

    def _reclaim_stale_lock(self) -> bool:
        """Remove a lock only when its owner is dead or old metadata is unusable."""
        try:
            stat = self.path.stat()
            original = self.path.read_bytes()
        except FileNotFoundError:
            return True
        except OSError:
            return False

        try:
            detail: Any = json.loads(original.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            detail = None

        stale = False
        if isinstance(detail, dict):
            pid = detail.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                owner_alive = self._pid_is_alive(pid)
                if owner_alive is False:
                    stale = True
                elif owner_alive is True or owner_alive is None:
                    return False

        if not stale:
            age_seconds = max(0.0, time.time() - stat.st_mtime)
            if age_seconds < self.stale_grace_seconds:
                return False

        try:
            if self.path.read_bytes() != original:
                return False
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _pid_is_alive(pid: int) -> bool | None:
        """Return None when process liveness cannot be determined safely."""
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            return ProfileLock._windows_pid_is_alive(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return False
            if exc.errno == errno.EPERM:
                return True
            return None
        return True

    @staticmethod
    def _windows_pid_is_alive(pid: int) -> bool | None:
        if pid > 0xFFFFFFFF:
            return False

        process_query_limited_information = 0x1000
        still_active = 259
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == error_invalid_parameter else None
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == still_active
        finally:
            close_handle(handle)

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
