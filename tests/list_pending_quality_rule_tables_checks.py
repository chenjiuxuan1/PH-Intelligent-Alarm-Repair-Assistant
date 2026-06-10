import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "list_pending_quality_rule_tables.py"
)


def load_module():
    fake_config_pkg = types.ModuleType("config")
    fake_config_module = types.ModuleType("config.config")
    fake_config_module.QUALITY_RULE_FORM_CONFIG = {
        "country": "ph",
        "confirmation_export_url": "https://example.com/export.csv",
        "confirmation_column_map": {},
    }

    fake_confirmation = types.ModuleType("core.quality_rule_confirmation")
    fake_confirmation.fetch_confirmation_csv = mock.MagicMock(return_value="")
    fake_confirmation.parse_confirmation_rows = mock.MagicMock(return_value=[])
    fake_confirmation.find_latest_confirmation_row = mock.MagicMock(return_value=None)

    fake_gap_scanner = types.ModuleType("core.quality_rule_gap_scanner")
    fake_gap_scanner.list_pending_generation_tables = mock.MagicMock(return_value=[])
    fake_gap_scanner.scan_quality_rule_gaps = mock.MagicMock(return_value=[])

    previous_modules = {
        "config": sys.modules.get("config"),
        "config.config": sys.modules.get("config.config"),
        "core.quality_rule_confirmation": sys.modules.get("core.quality_rule_confirmation"),
        "core.quality_rule_gap_scanner": sys.modules.get("core.quality_rule_gap_scanner"),
    }
    sys.modules["config"] = fake_config_pkg
    sys.modules["config.config"] = fake_config_module
    sys.modules["core.quality_rule_confirmation"] = fake_confirmation
    sys.modules["core.quality_rule_gap_scanner"] = fake_gap_scanner
    try:
        spec = importlib.util.spec_from_file_location(
            "list_pending_quality_rule_tables",
            str(MODULE_PATH),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in previous_modules.items():
            if previous is not None:
                sys.modules[name] = previous
            else:
                sys.modules.pop(name, None)


class ListPendingQualityRuleTablesChecks(unittest.TestCase):
    def test_filter_existing_confirmation_rows_skips_existing_sheet_items(self):
        module = load_module()
        confirmation_rows = [
            {"country": "ph", "database": "dwd", "tbl": "dwd_user_phone_md5", "submitted_at": "2026-06-09 18:00:00"}
        ]
        module.find_latest_confirmation_row = mock.MagicMock(
            side_effect=[
                confirmation_rows[0],
                None,
            ]
        )

        results = module.filter_existing_confirmation_rows(
            [
                {"database": "dwd", "tbl": "dwd_user_phone_md5"},
                {"database": "dwd", "tbl": "dwd_user_member_log"},
            ],
            confirmation_rows,
        )

        self.assertEqual(results, [{"database": "dwd", "tbl": "dwd_user_member_log"}])

    def test_main_metadata_mode_filters_out_existing_confirmation_rows(self):
        module = load_module()
        module.list_pending_generation_tables = mock.MagicMock(
            return_value=[
                {"database": "dwd", "tbl": "dwd_user_phone_md5", "status": "pending_generation"},
                {"database": "dwd", "tbl": "dwd_user_member_log", "status": "pending_generation"},
            ]
        )
        module.fetch_confirmation_csv = mock.MagicMock(return_value="csv")
        module.parse_confirmation_rows = mock.MagicMock(
            return_value=[
                {"country": "ph", "database": "dwd", "tbl": "dwd_user_phone_md5", "submitted_at": "2026-06-09 18:00:00"}
            ]
        )
        module.find_latest_confirmation_row = mock.MagicMock(
            side_effect=[
                {"country": "ph", "database": "dwd", "tbl": "dwd_user_phone_md5", "submitted_at": "2026-06-09 18:00:00"},
                None,
            ]
        )

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = [
            "list_pending_quality_rule_tables.py",
            "--database",
            "dwd",
            "--json",
        ]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(
            payload,
            [{"database": "dwd", "tbl": "dwd_user_member_log", "status": "pending_generation"}],
        )


if __name__ == "__main__":
    unittest.main()
