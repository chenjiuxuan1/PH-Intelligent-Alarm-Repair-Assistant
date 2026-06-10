import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_single_quality_rule_flow.py"


def load_module():
    fake_alert = types.ModuleType("alert")
    fake_db_module = types.ModuleType("alert.db_config")
    fake_db_module.get_db_connection = mock.MagicMock()

    previous_alert = sys.modules.get("alert")
    previous_alert_db = sys.modules.get("alert.db_config")
    sys.modules["alert"] = fake_alert
    sys.modules["alert.db_config"] = fake_db_module
    try:
        spec = importlib.util.spec_from_file_location("run_single_quality_rule_flow", str(MODULE_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_alert is not None:
            sys.modules["alert"] = previous_alert
        else:
            sys.modules.pop("alert", None)
        if previous_alert_db is not None:
            sys.modules["alert.db_config"] = previous_alert_db
        else:
            sys.modules.pop("alert.db_config", None)


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class RunSingleQualityRuleFlowTests(unittest.TestCase):
    def test_load_single_table_uses_ods_settings_for_ods_database(self):
        module = load_module()
        cursor = FakeCursor({"dest_tbl": "ods_demo"})

        row, config_table_name = module.load_single_table(cursor, "ods", "ods_demo")

        self.assertEqual(row["dest_tbl"], "ods_demo")
        self.assertEqual(config_table_name, "wattrel_ods_table_settings")
        self.assertIn("wattrel_ods_table_settings", cursor.executed[0][0])
        self.assertEqual(cursor.executed[0][1], ("ods", "ods_demo"))

    def test_load_single_table_uses_etl_settings_for_dwd_database(self):
        module = load_module()
        cursor = FakeCursor({"tbl": "dwd_demo"})

        row, config_table_name = module.load_single_table(cursor, "dwd", "dwd_demo")

        self.assertEqual(row["tbl"], "dwd_demo")
        self.assertEqual(config_table_name, "wattrel_etl_table_settings")
        self.assertIn("wattrel_etl_table_settings", cursor.executed[0][0])
        self.assertEqual(cursor.executed[0][1], ("dwd", "dwd_demo"))

    def test_main_does_not_send_tv_for_single_table_run(self):
        module = load_module()

        fake_conn = mock.MagicMock()
        fake_cursor = mock.MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_cursor.fetchone.return_value = {"tbl": "dwd_demo"}

        module.get_db_connection = mock.MagicMock(return_value=fake_conn)
        module.load_single_table = mock.MagicMock(return_value=({"tbl": "dwd_demo"}, "wattrel_etl_table_settings"))
        module.load_quality_rules = mock.MagicMock(return_value=[])
        module.load_ods_table_by_dest = mock.MagicMock(return_value={})
        module.build_count_rule_candidate = mock.MagicMock(
            return_value={
                "status": "blocked",
                "rule_name": "cnt",
                "dest_tbl": "dwd_demo",
                "dest_db": "dwd",
                "src_db": "ods",
                "src_tbl": "ods_demo",
                "src_sql": "select 1",
                "dest_sql": "select 2",
                "check_field": "input_date",
                "reason": "需要人工确认",
            }
        )
        module.load_backlog = mock.MagicMock(return_value={"items": {}})
        module.backlog_item_has_submittable_sql = mock.MagicMock(return_value=True)
        module.merge_candidates_into_backlog = mock.MagicMock(
            return_value=(
                {
                    "items": {
                        "dwd::dwd.dwd_demo::cnt": {
                            "candidate_key": "dwd::dwd.dwd_demo::cnt",
                            "status": "pending_confirmation",
                            "dest_tbl": "dwd_demo",
                            "src_sql": "select 1",
                            "dest_sql": "select 2",
                        }
                    }
                },
                [{"candidate_key": "dwd::dwd.dwd_demo::cnt"}],
            )
        )
        module.build_candidate_key = mock.MagicMock(return_value="dwd::dwd.dwd_demo::cnt")
        module.fetch_confirmation_csv = mock.MagicMock(return_value="database,tbl,metric_field\n")
        module.parse_confirmation_rows = mock.MagicMock(return_value=[])
        module.find_latest_confirmation_row = mock.MagicMock(return_value=None)
        module.find_latest_requested_metric_field = mock.MagicMock(return_value="")
        module.submit_backlog_items_to_form = mock.MagicMock(
            return_value={"submitted": 1, "results": [{"candidate_key": "dwd::dwd.dwd_demo::cnt", "ok": True}]}
        )
        module.compute_form_payload_signature = mock.MagicMock(return_value="sig")
        module.save_backlog = mock.MagicMock()
        module.load_langfuse_batch = mock.MagicMock(return_value={"batch": []})

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["run_single_quality_rule_flow.py", "--database", "dwd", "--tbl", "dwd_demo"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        payload_text = output.split("===FULL_CHAIN_RESULT===")[1].split("===LANGFUSE_BATCH===")[0].strip()
        payload = json.loads(payload_text)
        self.assertEqual(payload["tv_result"]["reason"], "deferred_batch_notification")
        self.assertEqual(payload["new_candidate_keys"], ["dwd::dwd.dwd_demo::cnt"])

    def test_main_does_not_submit_form_for_blocked_item_without_sql(self):
        module = load_module()

        fake_conn = mock.MagicMock()
        fake_cursor = mock.MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_cursor.fetchone.return_value = {"tbl": "ads_demo"}

        module.get_db_connection = mock.MagicMock(return_value=fake_conn)
        module.load_single_table = mock.MagicMock(return_value=({"tbl": "ads_demo", "db": "ads_sec"}, "wattrel_etl_table_settings"))
        module.load_quality_rules = mock.MagicMock(return_value=[])
        module.build_exists_rule_candidate = mock.MagicMock(
            return_value={
                "status": "blocked",
                "rule_name": "if_exists",
                "dest_tbl": "ads_demo",
                "dest_db": "ads_sec",
                "src_db": "",
                "src_tbl": "",
                "src_sql": "",
                "dest_sql": "",
                "reason": "无法可靠推断 ADS/ADS_SEC 的时间判定字段，已阻止使用 etl_create_time 兜底",
            }
        )
        module.load_backlog = mock.MagicMock(return_value={"items": {}})
        module.merge_candidates_into_backlog = mock.MagicMock(
            return_value=(
                {
                    "items": {
                        "ads_sec::ads_sec.ads_demo::if_exists": {
                            "candidate_key": "ads_sec::ads_sec.ads_demo::if_exists",
                            "status": "pending_confirmation",
                            "dest_tbl": "ads_demo",
                            "src_sql": "",
                            "dest_sql": "",
                            "rule_name": "if_exists",
                        }
                    }
                },
                [{"candidate_key": "ads_sec::ads_sec.ads_demo::if_exists"}],
            )
        )
        module.build_candidate_key = mock.MagicMock(return_value="ads_sec::ads_sec.ads_demo::if_exists")
        module.fetch_confirmation_csv = mock.MagicMock(return_value="database,tbl,metric_field\n")
        module.parse_confirmation_rows = mock.MagicMock(return_value=[])
        module.find_latest_confirmation_row = mock.MagicMock(return_value=None)
        module.find_latest_requested_metric_field = mock.MagicMock(return_value="")
        module.backlog_item_has_submittable_sql = mock.MagicMock(return_value=False)
        module.submit_backlog_items_to_form = mock.MagicMock()
        module.compute_form_payload_signature = mock.MagicMock(return_value="sig")
        module.save_backlog = mock.MagicMock()
        module.load_langfuse_batch = mock.MagicMock(return_value={"batch": []})

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["run_single_quality_rule_flow.py", "--database", "ads_sec", "--tbl", "ads_demo"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        payload_text = output.split("===FULL_CHAIN_RESULT===")[1].split("===LANGFUSE_BATCH===")[0].strip()
        payload = json.loads(payload_text)
        self.assertEqual(payload["form_submission_items"], 0)
        self.assertTrue(payload["form_result"]["skipped"])
        module.submit_backlog_items_to_form.assert_not_called()

    def test_main_passes_requested_metric_field_to_generation(self):
        module = load_module()

        fake_conn = mock.MagicMock()
        fake_cursor = mock.MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        module.get_db_connection = mock.MagicMock(return_value=fake_conn)
        module.load_single_table = mock.MagicMock(return_value=({"tbl": "dwd_demo"}, "wattrel_etl_table_settings"))
        module.load_quality_rules = mock.MagicMock(return_value=[])
        module.load_ods_table_by_dest = mock.MagicMock(return_value={})
        module.fetch_confirmation_csv = mock.MagicMock(return_value="database,tbl,metric_field\n")
        module.parse_confirmation_rows = mock.MagicMock(return_value=[{"database": "dwd", "tbl": "dwd_demo", "metric_field": "total_cost", "submitted_at": "2026-06-08 10:00:00"}])
        module.find_latest_confirmation_row = mock.MagicMock(return_value=None)
        module.find_latest_requested_metric_field = mock.MagicMock(return_value="total_cost")
        module.backlog_item_has_submittable_sql = mock.MagicMock(return_value=True)
        module.build_count_rule_candidate = mock.MagicMock(
            return_value={
                "status": "existing",
                "rule_name": "cnt",
                "dest_tbl": "dwd_demo",
                "dest_db": "dwd",
                "reason": "已存在 cnt 规则",
            }
        )
        module.load_langfuse_batch = mock.MagicMock(return_value={"batch": []})

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["run_single_quality_rule_flow.py", "--database", "dwd", "--tbl", "dwd_demo"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        self.assertEqual(module.build_count_rule_candidate.call_args.kwargs["requested_metric_field"], "total_cost")

    def test_main_skips_generation_when_confirmation_sheet_already_has_row(self):
        module = load_module()

        existing_row = {
            "country": "ph",
            "database": "dwd",
            "tbl": "dwd_demo",
            "need_apply": "1",
            "metric_field": "total_cost",
            "submitted_at": "2026-06-09 09:00:00",
            "sheet_row_number": 12,
        }
        module.fetch_confirmation_csv = mock.MagicMock(return_value="database,tbl,metric_field\n")
        module.parse_confirmation_rows = mock.MagicMock(return_value=[existing_row])
        module.find_latest_confirmation_row = mock.MagicMock(return_value=existing_row)
        module.find_latest_requested_metric_field = mock.MagicMock(return_value="total_cost")
        module.get_db_connection = mock.MagicMock()
        module.load_single_table = mock.MagicMock()
        module.load_quality_rules = mock.MagicMock()
        module.load_ods_table_by_dest = mock.MagicMock()
        module.build_count_rule_candidate = mock.MagicMock()
        module.merge_candidates_into_backlog = mock.MagicMock()
        module.submit_backlog_items_to_form = mock.MagicMock()
        module.save_backlog = mock.MagicMock()
        module.load_langfuse_batch = mock.MagicMock(return_value={"batch": []})

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = ["run_single_quality_rule_flow.py", "--database", "dwd", "--tbl", "dwd_demo"]
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            exit_code = module.main()
        finally:
            sys.argv = argv_backup
            sys.stdout = stdout_backup

        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        payload_text = output.split("===FULL_CHAIN_RESULT===")[1].split("===LANGFUSE_BATCH===")[0].strip()
        payload = json.loads(payload_text)
        self.assertEqual(payload["scan_result"]["status"], "skipped")
        self.assertIn("已存在该表记录", payload["scan_result"]["reason"])
        self.assertEqual(payload["scan_result"]["requested_metric_field"], "total_cost")
        module.get_db_connection.assert_not_called()
        module.build_count_rule_candidate.assert_not_called()
        module.merge_candidates_into_backlog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
