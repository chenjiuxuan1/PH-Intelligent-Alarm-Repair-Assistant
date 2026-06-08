import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "apply_confirmed_quality_rules.py"


def load_module():
    fake_config = types.ModuleType("config")
    fake_config_config = types.ModuleType("config.config")
    fake_config_config.QUALITY_RULE_FORM_CONFIG = {
        "confirmation_export_url": "https://docs.google.com/spreadsheets/d/export?format=csv",
        "confirmation_column_map": {
            "candidate_key": "candidate_key",
            "database": "database",
            "tbl": "tbl",
            "need_apply": "need_apply",
            "human_check": "human_check",
            "src_sql": "src_sql",
            "dest_sql": "dest_sql",
            "operator": "operator",
            "notes": "notes",
            "submitted_at": "submitted_at",
        },
        "notify_mentions": [],
        "notify_bot_id": "quality-test-bot",
    }

    fake_confirmation = types.ModuleType("core.quality_rule_confirmation")
    fake_confirmation.format_tv_apply_summary = mock.MagicMock(return_value="summary")
    fake_confirmation.fetch_confirmation_csv = mock.MagicMock(return_value="")
    fake_confirmation.load_backlog = mock.MagicMock(return_value={"items": {}})
    fake_confirmation.parse_confirmation_rows = mock.MagicMock(return_value=[])
    fake_confirmation.save_backlog = mock.MagicMock()
    fake_confirmation.update_backlog_with_decisions = mock.MagicMock(return_value=([], []))

    fake_gap = types.ModuleType("core.quality_rule_gap_scanner")
    fake_gap.apply_candidates = mock.MagicMock(return_value=0)
    fake_gap.disable_auto_check_for_items = mock.MagicMock(return_value=0)
    fake_gap.validate_candidates = mock.MagicMock(return_value={"passed": [], "failed": []})

    previous_config = sys.modules.get("config")
    previous_config_config = sys.modules.get("config.config")
    previous_confirmation = sys.modules.get("core.quality_rule_confirmation")
    previous_gap = sys.modules.get("core.quality_rule_gap_scanner")
    sys.modules["config"] = fake_config
    sys.modules["config.config"] = fake_config_config
    sys.modules["core.quality_rule_confirmation"] = fake_confirmation
    sys.modules["core.quality_rule_gap_scanner"] = fake_gap
    try:
        spec = importlib.util.spec_from_file_location("apply_confirmed_quality_rules", str(MODULE_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, fake_confirmation, fake_gap
    finally:
        if previous_config is not None:
            sys.modules["config"] = previous_config
        else:
            sys.modules.pop("config", None)
        if previous_config_config is not None:
            sys.modules["config.config"] = previous_config_config
        else:
            sys.modules.pop("config.config", None)
        if previous_confirmation is not None:
            sys.modules["core.quality_rule_confirmation"] = previous_confirmation
        else:
            sys.modules.pop("core.quality_rule_confirmation", None)
        if previous_gap is not None:
            sys.modules["core.quality_rule_gap_scanner"] = previous_gap
        else:
            sys.modules.pop("core.quality_rule_gap_scanner", None)


class ApplyConfirmedQualityRulesTests(unittest.TestCase):
    def test_main_reads_confirmation_rows_from_local_csv_file(self):
        module, fake_confirmation, fake_gap = load_module()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".csv") as handle:
            handle.write("candidate_key,database,tbl,need_apply,human_check,src_sql,dest_sql,operator,notes,submitted_at\n")
            handle.write("dwd::dwd.demo::cnt,dwd,demo,1,1,select 1,select 2,me,,2026-06-08 12:00:00\n")
            csv_path = handle.name

        fake_confirmation.parse_confirmation_rows.return_value = [
            {
                "candidate_key": "dwd::dwd.demo::cnt",
                "database": "dwd",
                "tbl": "demo",
                "need_apply": "1",
                "human_check": "1",
                "src_sql": "select 1",
                "dest_sql": "select 2",
                "operator": "me",
                "notes": "",
                "submitted_at": "2026-06-08 12:00:00",
            }
        ]
        fake_confirmation.load_backlog.return_value = {
            "items": {
                "dwd::dwd.demo::cnt": {
                    "candidate_key": "dwd::dwd.demo::cnt",
                    "country": "ph",
                    "database": "dwd",
                    "dest_db": "dwd",
                    "dest_tbl": "demo",
                    "rule_name": "cnt",
                    "src_db": "ods",
                    "src_tbl": "demo",
                    "src_sql": "select old",
                    "dest_sql": "select old2",
                    "status": "pending_confirmation",
                    "applied_at": "",
                }
            }
        }
        fake_confirmation.update_backlog_with_decisions.return_value = (
            [fake_confirmation.load_backlog.return_value["items"]["dwd::dwd.demo::cnt"]],
            [],
        )
        fake_gap.validate_candidates.return_value = {
            "passed": [{"candidate": {"candidate_key": "dwd::dwd.demo::cnt"}}],
            "failed": [],
        }
        fake_gap.apply_candidates.return_value = 1

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["apply_confirmed_quality_rules.py", "--csv-file", csv_path, "--json"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup
            Path(csv_path).unlink(missing_ok=True)

        self.assertEqual(exit_code, 0)
        fake_confirmation.fetch_confirmation_csv.assert_not_called()
        fake_confirmation.parse_confirmation_rows.assert_called_once()
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["applied_count"], 1)


if __name__ == "__main__":
    unittest.main()
