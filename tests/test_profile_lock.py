import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from douyin_creator_mcp.browser.profile_lock import ProfileLock
from douyin_creator_mcp.errors import AppError, PROFILE_IN_USE


class ProfileLockTests(unittest.TestCase):
    def test_only_one_process_owner_can_hold_profile_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = ProfileLock(Path(tmp))
            second = ProfileLock(Path(tmp))
            first.acquire()
            try:
                with self.assertRaises(AppError) as caught:
                    second.acquire()
                self.assertEqual(caught.exception.error_type, PROFILE_IN_USE)
                self.assertTrue(first.inspect()["locked"])
            finally:
                first.release()
            self.assertFalse(second.inspect()["locked"])

    def test_dead_process_lock_is_reclaimed_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".douyin-mcp.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "owner": "crashed-owner",
                        "pid": 999999,
                        "acquired_at": "2026-07-13T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            lock = ProfileLock(Path(tmp))

            with patch.object(ProfileLock, "_pid_is_alive", return_value=False):
                lock.acquire()

            current = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(current["owner"], lock.owner)
            self.assertEqual(current["pid"], os.getpid())
            lock.release()
            self.assertFalse(lock_path.exists())

    def test_fresh_malformed_lock_is_not_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".douyin-mcp.lock"
            lock_path.write_text("incomplete", encoding="utf-8")
            lock = ProfileLock(Path(tmp), stale_grace_seconds=60)

            with self.assertRaises(AppError) as caught:
                lock.acquire()

            self.assertEqual(caught.exception.error_type, PROFILE_IN_USE)
            self.assertEqual(lock_path.read_text(encoding="utf-8"), "incomplete")

    def test_old_malformed_lock_is_reclaimed_after_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".douyin-mcp.lock"
            lock_path.write_text("incomplete", encoding="utf-8")
            lock = ProfileLock(Path(tmp), stale_grace_seconds=0)

            lock.acquire()

            current = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(current["owner"], lock.owner)
            lock.release()

    def test_unknown_process_state_keeps_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".douyin-mcp.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "owner": "unknown-owner",
                        "pid": 999999,
                        "acquired_at": "2026-07-13T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            lock = ProfileLock(Path(tmp), stale_grace_seconds=0)

            with patch.object(ProfileLock, "_pid_is_alive", return_value=None):
                with self.assertRaises(AppError) as caught:
                    lock.acquire()

            self.assertEqual(caught.exception.error_type, PROFILE_IN_USE)
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["owner"],
                "unknown-owner",
            )

    def test_recovery_does_not_delete_a_lock_replaced_during_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".douyin-mcp.lock"
            original = json.dumps(
                {"owner": "dead-owner", "pid": 999999}
            ).encode("utf-8")
            replacement = json.dumps(
                {"owner": "new-owner", "pid": os.getpid()}
            ).encode("utf-8")
            lock_path.write_bytes(original)
            lock = ProfileLock(Path(tmp))
            read_count = 0

            def read_with_replacement(path: Path) -> bytes:
                nonlocal read_count
                read_count += 1
                if read_count == 1:
                    return original
                path.write_bytes(replacement)
                return replacement

            with (
                patch.object(ProfileLock, "_pid_is_alive", return_value=False),
                patch.object(
                    Path,
                    "read_bytes",
                    autospec=True,
                    side_effect=read_with_replacement,
                ),
            ):
                self.assertFalse(lock._reclaim_stale_lock())

            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["owner"],
                "new-owner",
            )

    def test_current_process_is_reported_alive(self) -> None:
        self.assertTrue(ProfileLock._pid_is_alive(os.getpid()))

    def test_exited_process_is_reported_dead(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait(timeout=5)

        self.assertFalse(ProfileLock._pid_is_alive(process.pid))

    def test_failed_lock_write_does_not_leave_corrupt_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = ProfileLock(Path(tmp))

            with patch(
                "douyin_creator_mcp.browser.profile_lock.os.fsync",
                side_effect=OSError("disk failure"),
            ):
                with self.assertRaises(OSError):
                    lock.acquire()

            self.assertFalse(lock.path.exists())
