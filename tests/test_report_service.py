from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from douyin_creator_mcp.api_mapping import load_api_mapping
from douyin_creator_mcp.config import Settings, generate_token_key
from douyin_creator_mcp.services.account_service import AccountService
from douyin_creator_mcp.services.capability_service import CapabilityService
from douyin_creator_mcp.services.report_service import ReportService
from douyin_creator_mcp.storage.db import Database


class ReportServiceTests(unittest.TestCase):
    def test_degraded_report_marks_missing_data_as_permission_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                data_dir=Path(tmp),
                token_encryption_key=generate_token_key(),
                api_mapping_file=ROOT / "docs" / "api-mapping.md",
            )
            db = Database(Path(tmp) / "douyin.sqlite")
            db.init_schema()
            account_service = AccountService(db)
            account_service.upsert_account({"id": "open-1", "open_id": "open-1", "nickname": "creator"}, ["user_info"])
            capability_service = CapabilityService(db, load_api_mapping(settings.api_mapping_file), account_service)
            capability_service.check_capabilities("open-1")
            report_service = ReportService(settings, db)

            result = report_service.generate_creator_report("open-1", "7d")
            report_path = Path(result["report_path"])
            content = report_path.read_text(encoding="utf-8")

            self.assertTrue(report_path.exists())
            self.assertEqual(result["summary"]["data_quality"]["level"], "limited")
            self.assertIn("不代表账号表现不好", content)
            self.assertIn("video.data", str(result["summary"]["capabilities"]))


if __name__ == "__main__":
    unittest.main()
