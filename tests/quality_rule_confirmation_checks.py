import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "quality_rule_confirmation.py"


def load_module():
    fake_config = types.ModuleType("config")
    fake_config_config = types.ModuleType("config.config")
    fake_config_config.QUALITY_RULE_FORM_CONFIG = {
        "country": "ph",
        "view_url": "https://docs.google.com/forms/d/e/test/viewform",
        "post_url": "https://docs.google.com/forms/d/e/test/formResponse",
        "confirmation_sheet_url": "https://docs.google.com/spreadsheets/d/test/edit#gid=1",
        "field_map": {
            "submission_type": "entry.1",
            "candidate_key": "entry.2",
            "country": "entry.3",
            "database": "entry.4",
            "tbl": "entry.5",
            "need_apply": "entry.6",
            "src_sql": "entry.7",
            "dest_sql": "entry.8",
            "human_check": "entry.9",
        },
        "required_fields": ["submission_type", "candidate_key", "country", "database", "tbl", "need_apply"],
        "confirmation_export_url": "https://docs.google.com/spreadsheets/d/export?format=csv",
        "confirmation_column_map": {
            "submission_type": "submission_type",
            "candidate_key": "candidate_key",
            "country": "country",
            "database": "database",
            "tbl": "tbl",
            "need_apply": "need_apply",
            "src_sql": "src_sql",
            "dest_sql": "dest_sql",
            "human_check": "human_check",
            "operator": "operator",
            "notes": "notes",
            "submitted_at": "Timestamp",
        },
        "notify_bot_id": "quality-test-bot",
        "notify_mentions": ["owner@example.com"],
        "git_scan_roots": ["/data/git"],
    }
    fake_config_config.WORKSPACE_CONFIG = {
        "quality_rule_backlog_file": "/tmp/test-quality-backlog.json",
        "quality_rule_sync_state_file": "/tmp/test-quality-sync-state.json",
    }
    fake_gap_scanner = types.ModuleType("core.quality_rule_gap_scanner")
    fake_gap_scanner.resolve_rule_name = lambda database: "if_exists" if database.startswith("ads") else "cnt"
    fake_send_tv_report = types.ModuleType("core.send_tv_report")
    fake_send_tv_report.send_tv_report = mock.MagicMock(return_value={"success": True, "status_code": 202})

    previous_config = sys.modules.get("config")
    previous_config_config = sys.modules.get("config.config")
    previous_gap_scanner = sys.modules.get("core.quality_rule_gap_scanner")
    previous_send_tv = sys.modules.get("core.send_tv_report")
    sys.modules["config"] = fake_config
    sys.modules["config.config"] = fake_config_config
    sys.modules["core.quality_rule_gap_scanner"] = fake_gap_scanner
    sys.modules["core.send_tv_report"] = fake_send_tv_report
    try:
        spec = importlib.util.spec_from_file_location("quality_rule_confirmation", str(MODULE_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, fake_send_tv_report
    finally:
        if previous_config is not None:
            sys.modules["config"] = previous_config
        else:
            sys.modules.pop("config", None)
        if previous_config_config is not None:
            sys.modules["config.config"] = previous_config_config
        else:
            sys.modules.pop("config.config", None)
        if previous_gap_scanner is not None:
            sys.modules["core.quality_rule_gap_scanner"] = previous_gap_scanner
        else:
            sys.modules.pop("core.quality_rule_gap_scanner", None)
        if previous_send_tv is not None:
            sys.modules["core.send_tv_report"] = previous_send_tv
        else:
            sys.modules.pop("core.send_tv_report", None)


class QualityRuleConfirmationTests(unittest.TestCase):
    def make_candidate_result(self):
        return {
            "status": "candidate",
            "database": "dwd",
            "rule_name": "cnt",
            "dest_db": "dwd",
            "dest_tbl": "dwd_user_member_log",
            "reason": "可自动生成 cnt 规则",
            "candidate": {
                "src_db": "ods",
                "src_tbl": "ods_user_member_log",
                "check_field": "etl_create_time",
                "src_sql": "select 1",
                "dest_sql": "select 2",
                "git_matches": ["/data/git/dwd_user_member_log.sql"],
            },
        }

    def test_merge_candidates_into_backlog_dedupes_by_candidate_key(self):
        module, _ = load_module()
        backlog = {"items": {}}
        result = self.make_candidate_result()

        backlog, new_items = module.merge_candidates_into_backlog([result], backlog=backlog, detected_at="2026-06-04 12:00:00")
        backlog, second_new_items = module.merge_candidates_into_backlog([result], backlog=backlog, detected_at="2026-06-04 12:00:00")

        self.assertEqual(len(new_items), 1)
        self.assertEqual(second_new_items, [])
        self.assertEqual(len(backlog["items"]), 1)

    def test_format_tv_confirmation_message_includes_sheet_url_and_candidate_key(self):
        module, _ = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")

        message = module.format_tv_confirmation_message([backlog_item], confirmation_sheet_url="https://docs.google.com/spreadsheets/d/test/edit#gid=1")

        self.assertIn("质量规则待补充确认", message)
        self.assertIn(backlog_item["dest_tbl"], message)
        self.assertIn("https://docs.google.com/spreadsheets/d/test/edit#gid=1", message)
        self.assertIn("确认响应表", message)

    def test_get_pending_form_submission_items_returns_unsubmitted_pending_items(self):
        module, _ = load_module()
        pending_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        submitted_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:01:00")
        submitted_item["candidate_key"] = "dwd::dwd.other_table::cnt"
        submitted_item["form_submitted_at"] = "2026-06-04 12:02:00"
        approved_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:03:00")
        approved_item["candidate_key"] = "dwd::dwd.third_table::cnt"
        approved_item["status"] = "approved"
        backlog = {
            "items": {
                pending_item["candidate_key"]: pending_item,
                submitted_item["candidate_key"]: submitted_item,
                approved_item["candidate_key"]: approved_item,
            }
        }

        pending_items = module.get_pending_form_submission_items(backlog)

        self.assertEqual([item["candidate_key"] for item in pending_items], [pending_item["candidate_key"]])

    def test_get_pending_form_submission_items_can_include_already_submitted_items(self):
        module, _ = load_module()
        pending_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        submitted_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:01:00")
        submitted_item["candidate_key"] = "dwd::dwd.other_table::cnt"
        submitted_item["form_submitted_at"] = "2026-06-04 12:02:00"
        backlog = {
            "items": {
                pending_item["candidate_key"]: pending_item,
                submitted_item["candidate_key"]: submitted_item,
            }
        }

        pending_items = module.get_pending_form_submission_items(backlog, include_submitted=True)

        self.assertEqual(
            [item["candidate_key"] for item in pending_items],
            [pending_item["candidate_key"], submitted_item["candidate_key"]],
        )

    def test_notify_new_candidates_via_tv_uses_shared_mentions(self):
        module, fake_send_tv = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")

        result = module.notify_new_candidates_via_tv([backlog_item])

        self.assertTrue(result["success"])
        args, kwargs = fake_send_tv.send_tv_report.call_args
        self.assertIn("质量规则待补充确认", args[0])
        self.assertEqual(kwargs["mentions"], ["owner@example.com"])
        self.assertEqual(kwargs["bot_id"], "quality-test-bot")

    def test_parse_confirmation_rows_maps_csv_headers(self):
        module, _ = load_module()
        csv_text = "Timestamp,submission_type,candidate_key,country,database,tbl,need_apply,src_sql,dest_sql,human_check,operator,notes\n2026-06-04 18:00:00,decision,dwd::dwd.dwd_user_member_log::cnt,ph,dwd,dwd_user_member_log,1,select 1,select 2,1,alice,ok\n"

        rows = module.parse_confirmation_rows(csv_text, module.QUALITY_RULE_FORM_CONFIG["confirmation_column_map"])

        self.assertEqual(rows[0]["candidate_key"], "dwd::dwd.dwd_user_member_log::cnt")
        self.assertEqual(rows[0]["need_apply"], "1")
        self.assertEqual(rows[0]["human_check"], "1")
        self.assertEqual(rows[0]["operator"], "alice")

    def test_update_backlog_with_decisions_marks_approved_items(self):
        module, _ = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        backlog = {"items": {backlog_item["candidate_key"]: backlog_item}}
        decision_rows = [
            {
                "submission_type": "decision",
                "candidate_key": backlog_item["candidate_key"],
                "country": "ph",
                "database": "dwd",
                "tbl": "dwd_user_member_log",
                "need_apply": "1",
                "src_sql": "select override_src",
                "dest_sql": "select override_dest",
                "human_check": "1",
                "operator": "alice",
                "notes": "please apply",
                "submitted_at": "2026-06-04 18:00:00",
            }
        ]

        approved, rejected = module.update_backlog_with_decisions(backlog, decision_rows)

        self.assertEqual(len(approved), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(backlog_item["status"], "approved")
        self.assertEqual(backlog_item["decision_src_sql"], "select override_src")
        self.assertEqual(backlog_item["decision_dest_sql"], "select override_dest")
        self.assertEqual(backlog_item["decision_operator"], "alice")

    def test_build_form_payload_requires_candidate_key(self):
        module, _ = load_module()

        with self.assertRaises(ValueError):
            module.build_form_payload(
                {"submission_type": "detected"},
                module.QUALITY_RULE_FORM_CONFIG["field_map"],
                required_fields=module.QUALITY_RULE_FORM_CONFIG["required_fields"],
            )

    def test_update_backlog_with_need_apply_zero_marks_item_rejected(self):
        module, _ = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        backlog = {"items": {backlog_item["candidate_key"]: backlog_item}}
        decision_rows = [
            {
                "submission_type": "decision",
                "country": "ph",
                "database": "dwd",
                "tbl": "dwd_user_member_log",
                "need_apply": "0",
                "human_check": "1",
                "operator": "alice",
                "submitted_at": "2026-06-04 18:00:00",
            }
        ]

        approved, rejected = module.update_backlog_with_decisions(backlog, decision_rows)

        self.assertEqual(approved, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(backlog_item["status"], "rejected")

    def test_update_backlog_accepts_rows_without_operator(self):
        module, _ = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        backlog = {"items": {backlog_item["candidate_key"]: backlog_item}}
        decision_rows = [
            {
                "submission_type": "detected",
                "country": "ph",
                "database": "dwd",
                "tbl": "dwd_user_member_log",
                "need_apply": "1",
                "human_check": "1",
                "operator": "",
                "submitted_at": "2026-06-04 18:00:00",
            }
        ]

        approved, rejected = module.update_backlog_with_decisions(backlog, decision_rows)

        self.assertEqual(len(approved), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(backlog_item["status"], "approved")

    def test_update_backlog_ignores_rows_without_human_check(self):
        module, _ = load_module()
        backlog_item = module.candidate_to_backlog_item(self.make_candidate_result(), detected_at="2026-06-04 12:00:00")
        backlog = {"items": {backlog_item["candidate_key"]: backlog_item}}
        decision_rows = [
            {
                "submission_type": "decision",
                "candidate_key": backlog_item["candidate_key"],
                "country": "ph",
                "database": "dwd",
                "tbl": "dwd_user_member_log",
                "need_apply": "1",
                "human_check": "0",
                "submitted_at": "2026-06-04 18:00:00",
            }
        ]

        approved, rejected = module.update_backlog_with_decisions(backlog, decision_rows)

        self.assertEqual(approved, [])
        self.assertEqual(rejected, [])
        self.assertEqual(backlog_item["status"], "pending_confirmation")


if __name__ == "__main__":
    unittest.main()
