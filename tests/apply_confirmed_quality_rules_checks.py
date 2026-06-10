import importlib.util
import io
import json
import base64
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
            "metric_field": "metric_field",
        },
        "notify_mentions": [],
        "notify_bot_id": "quality-test-bot",
    }

    fake_confirmation = types.ModuleType("core.quality_rule_confirmation")
    fake_confirmation.format_tv_apply_summary = mock.MagicMock(return_value="summary")
    fake_confirmation.extract_sheet_row_number = mock.MagicMock(
        side_effect=lambda row: row.get("decision_sheet_row_number")
        or row.get("sheet_row_number")
        or row.get("row_number")
    )
    fake_confirmation.fetch_confirmation_csv = mock.MagicMock(return_value="")
    fake_confirmation.filter_unprocessed_decision_rows = mock.MagicMock(side_effect=lambda rows, sync_state=None: rows)
    fake_confirmation.load_backlog = mock.MagicMock(return_value={"items": {}})
    fake_confirmation.load_sync_state = mock.MagicMock(return_value={})
    fake_confirmation.mark_processed_decisions = mock.MagicMock(side_effect=lambda state, items, action: state)
    fake_confirmation.parse_confirmation_rows = mock.MagicMock(return_value=[])
    fake_confirmation.delete_confirmation_sheet_rows = mock.MagicMock(
        return_value={"success": True, "deleted_rows": [], "skipped": True, "reason": "no_rows"}
    )
    fake_confirmation.remove_backlog_items = mock.MagicMock(side_effect=lambda backlog, keys: backlog)
    fake_confirmation.save_backlog = mock.MagicMock()
    fake_confirmation.save_sync_state = mock.MagicMock()
    fake_confirmation.update_backlog_with_decisions = mock.MagicMock(return_value=([], []))

    fake_gap = types.ModuleType("core.quality_rule_gap_scanner")
    fake_gap.apply_candidates = mock.MagicMock(return_value=0)
    fake_gap.disable_auto_check_for_items = mock.MagicMock(return_value=0)
    fake_gap.validate_candidates_for_apply = mock.MagicMock(return_value={"passed": [], "failed": []})

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
                "sheet_row_number": 2,
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
            [{**fake_confirmation.load_backlog.return_value["items"]["dwd::dwd.demo::cnt"], "status": "approved", "decision_sheet_row_number": 2}],
            [],
        )
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
        self.assertEqual(payload["processed_sheet_rows"], [2])
        self.assertEqual(payload["sheet_delete_result"]["deleted_rows"], [])
        fake_confirmation.delete_confirmation_sheet_rows.assert_called_once_with([2])
        fake_gap.validate_candidates_for_apply.assert_not_called()

    def test_main_reads_confirmation_rows_from_base64_json(self):
        module, fake_confirmation, fake_gap = load_module()

        decision_rows = [
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
                "row_number": 8,
            }
        ]
        payload_b64 = base64.b64encode(json.dumps(decision_rows, ensure_ascii=False).encode("utf-8")).decode("utf-8")

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
            [{**fake_confirmation.load_backlog.return_value["items"]["dwd::dwd.demo::cnt"], "status": "approved", "row_number": 8}],
            [],
        )
        fake_gap.apply_candidates.return_value = 1

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["apply_confirmed_quality_rules.py", "--decision-json-base64", payload_b64, "--json"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        fake_confirmation.fetch_confirmation_csv.assert_not_called()
        fake_confirmation.parse_confirmation_rows.assert_not_called()
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["applied_count"], 1)
        self.assertEqual(payload["applied_sheet_rows"], [8])
        fake_confirmation.delete_confirmation_sheet_rows.assert_called_once_with([8])
        fake_gap.validate_candidates_for_apply.assert_not_called()

    def test_main_does_not_validate_by_default(self):
        module, fake_confirmation, fake_gap = load_module()

        decision_rows = [
            {
                "candidate_key": "dwd::dwd.demo::cnt",
                "database": "dwd",
                "tbl": "demo",
                "need_apply": "1",
                "human_check": "1",
                "src_sql": "select broken",
                "dest_sql": "select broken2",
                "operator": "me",
                "notes": "",
                "submitted_at": "2026-06-08 12:00:00",
            }
        ]
        payload_b64 = base64.b64encode(json.dumps(decision_rows, ensure_ascii=False).encode("utf-8")).decode("utf-8")

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
            [{**fake_confirmation.load_backlog.return_value["items"]["dwd::dwd.demo::cnt"], "status": "approved"}],
            [],
        )
        fake_gap.apply_candidates.return_value = 1

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["apply_confirmed_quality_rules.py", "--decision-json-base64", payload_b64, "--json"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["applied_count"], 1)
        self.assertEqual(payload["validation_failed_count"], 0)
        self.assertEqual(payload["processed_sheet_rows"], [])
        fake_confirmation.delete_confirmation_sheet_rows.assert_called_once_with([])
        fake_gap.validate_candidates_for_apply.assert_not_called()
        fake_gap.apply_candidates.assert_called_once()
        fake_confirmation.mark_processed_decisions.assert_any_call(mock.ANY, mock.ANY, action="applied")
        fake_confirmation.remove_backlog_items.assert_called_once()
        fake_confirmation.save_sync_state.assert_called_once()

    def test_main_honors_validate_syntax_flag(self):
        module, fake_confirmation, fake_gap = load_module()

        decision_rows = [
            {
                "candidate_key": "dwd::dwd.demo::cnt",
                "database": "dwd",
                "tbl": "demo",
                "need_apply": "1",
                "human_check": "1",
                "src_sql": "select broken",
                "dest_sql": "select broken2",
                "operator": "me",
                "notes": "",
                "submitted_at": "2026-06-08 12:00:00",
            }
        ]
        payload_b64 = base64.b64encode(json.dumps(decision_rows, ensure_ascii=False).encode("utf-8")).decode("utf-8")

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
        fake_gap.validate_candidates_for_apply.return_value = {
            "passed": [],
            "failed": [{"candidate": {"candidate_key": "dwd::dwd.demo::cnt"}, "reason": "SQL 语法校验失败: syntax error"}],
        }

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["apply_confirmed_quality_rules.py", "--decision-json-base64", payload_b64, "--validate-syntax", "--json"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["applied_count"], 0)
        self.assertEqual(payload["validation_failed_count"], 1)
        self.assertEqual(payload["processed_sheet_rows"], [])
        fake_confirmation.delete_confirmation_sheet_rows.assert_called_once_with([])
        fake_gap.validate_candidates_for_apply.assert_called_once()
        fake_gap.apply_candidates.assert_called_once_with([])
        fake_confirmation.save_sync_state.assert_not_called()

    def test_main_filters_already_processed_decisions(self):
        module, fake_confirmation, fake_gap = load_module()

        decision_rows = [
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
        payload_b64 = base64.b64encode(json.dumps(decision_rows, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        fake_confirmation.filter_unprocessed_decision_rows.side_effect = lambda rows, sync_state=None: []

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["apply_confirmed_quality_rules.py", "--decision-json-base64", payload_b64, "--json"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["approved_candidates"], 0)
        self.assertEqual(payload["applied_count"], 0)
        self.assertEqual(payload["processed_sheet_rows"], [])
        fake_confirmation.delete_confirmation_sheet_rows.assert_called_once_with([])
        fake_confirmation.update_backlog_with_decisions.assert_called_once_with({"items": {}}, [])
        fake_gap.apply_candidates.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
