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
    fake_confirmation.confirmation_row_has_submittable_sql = mock.MagicMock(return_value=False)
    fake_confirmation.auto_generate_is_enabled = mock.MagicMock(
        side_effect=lambda value: str(value).strip().lower() in {"1", "true", "yes"}
    )

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
        existing_row = confirmation_rows[0]
        module.find_latest_confirmation_row = mock.MagicMock(
            side_effect=[
                existing_row,
                None,
            ]
        )
        module.confirmation_row_has_submittable_sql = mock.MagicMock(
            side_effect=[True, False]
        )

        results = module.filter_existing_confirmation_rows(
            [
                {"database": "dwd", "tbl": "dwd_user_phone_md5"},
                {"database": "dwd", "tbl": "dwd_user_member_log"},
            ],
            confirmation_rows,
        )

        self.assertEqual(results, [{"database": "dwd", "tbl": "dwd_user_member_log"}])

    def test_filter_existing_confirmation_rows_keeps_manual_sheet_item_without_sql(self):
        module = load_module()
        existing_row = {
            "country": "ph",
            "database": "dwd",
            "tbl": "dwd_user_phone_md5",
            "auto_generate": "1",
            "src_sql": "",
            "dest_sql": "",
            "submitted_at": "2026-06-09 18:00:00",
        }
        module.find_latest_confirmation_row = mock.MagicMock(return_value=existing_row)
        module.confirmation_row_has_submittable_sql = mock.MagicMock(return_value=False)

        results = module.filter_existing_confirmation_rows(
            [{"database": "dwd", "tbl": "dwd_user_phone_md5"}],
            [existing_row],
        )

        self.assertEqual(results, [{"database": "dwd", "tbl": "dwd_user_phone_md5"}])

    def test_extract_manual_pending_rows_includes_hand_filled_generation_requests(self):
        module = load_module()
        confirmation_rows = [
            {
                "country": "ph",
                "database": "ads",
                "tbl": "ads_demo",
                "auto_generate": "1",
                "src_sql": "",
                "dest_sql": "",
            },
            {
                "country": "ph",
                "database": "dwd",
                "tbl": "dwd_done",
                "auto_generate": "1",
                "src_sql": "select 1",
                "dest_sql": "select 2",
            },
        ]
        module.confirmation_row_has_submittable_sql = mock.MagicMock(
            side_effect=[False, True]
        )

        results = module.extract_manual_pending_rows(confirmation_rows, "ph")

        self.assertEqual(
            results,
            [
                {
                    "database": "ads",
                    "tbl": "ads_demo",
                    "status": "pending_generation",
                    "reason": "Google 确认表手动录入，待自动生成",
                    "source": "confirmation_sheet",
                }
            ],
        )

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
        module.confirmation_row_has_submittable_sql = mock.MagicMock(
            side_effect=[True, False]
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

    def test_main_metadata_mode_includes_manual_confirmation_rows_without_sql(self):
        module = load_module()
        module.list_pending_generation_tables = mock.MagicMock(return_value=[])
        module.fetch_confirmation_csv = mock.MagicMock(return_value="csv")
        module.parse_confirmation_rows = mock.MagicMock(
            return_value=[
                {
                    "country": "ph",
                    "database": "ads",
                    "tbl": "ads_manual_demo",
                    "auto_generate": "1",
                    "src_sql": "",
                    "dest_sql": "",
                    "submitted_at": "2026-06-09 18:00:00",
                }
            ]
        )
        module.confirmation_row_has_submittable_sql = mock.MagicMock(return_value=False)
        module.find_latest_confirmation_row = mock.MagicMock(return_value=None)

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = [
            "list_pending_quality_rule_tables.py",
            "--database",
            "ads",
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
            [
                {
                    "database": "ads",
                    "tbl": "ads_manual_demo",
                    "status": "pending_generation",
                    "reason": "Google 确认表手动录入，待自动生成",
                    "source": "confirmation_sheet",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
