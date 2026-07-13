from pathlib import Path
import tempfile
import unittest

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
