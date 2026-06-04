import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "quality_rule_gap_scanner.py"


def load_module(fake_get_db_connection=None):
    fake_alert = types.ModuleType("alert")
    fake_db_module = types.ModuleType("alert.db_config")
    fake_db_module.get_db_connection = fake_get_db_connection or mock.MagicMock()

    previous_alert = sys.modules.get("alert")
    previous_alert_db = sys.modules.get("alert.db_config")
    sys.modules["alert"] = fake_alert
    sys.modules["alert.db_config"] = fake_db_module
    try:
        spec = importlib.util.spec_from_file_location("quality_rule_gap_scanner", str(MODULE_PATH))
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
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []
        self._current_rows = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if not self.responses:
            self._current_rows = []
            return
        self._current_rows = self.responses.pop(0)

    def fetchall(self):
        return self._current_rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class QualityRuleGapScannerTests(unittest.TestCase):
    def test_determine_create_field_prefers_created_at_like_columns(self):
        module = load_module()
        table = {
            "src_tbl": "user_member_log",
            "columns": json.dumps(["id", "created_at", "other_field"]),
        }

        self.assertEqual(module.determine_create_field(table), "created_at")

    def test_build_count_rule_candidate_returns_existing_when_cnt_rule_present(self):
        module = load_module()
        table = {"db": "dwd", "tbl": "dwd_user_log"}
        rule_map = {
            "dwd_user_log": {
                "cnt": {"name": "cnt", "dest_db": "dwd", "dest_tbl": "dwd_user_log"}
            }
        }

        result = module.build_count_rule_candidate("dwd", table, rule_map, {})

        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["rule_name"], "cnt")

    def test_build_count_rule_candidate_generates_candidate_for_etl_table(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_user_member_log",
            "dep_tbls": json.dumps(["ods_user_member_log"]),
            "increment_field": "etl_create_time",
            "check_field": "",
        }

        result = module.build_count_rule_candidate("dwd", table, {}, {})

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["src_db"], "ods")
        self.assertEqual(result["candidate"]["src_tbl"], "ods_user_member_log")
        self.assertEqual(result["candidate"]["check_field"], "etl_create_time")
        self.assertIn("SELECT COUNT(*)", result["candidate"]["src_sql"])

    def test_build_count_rule_candidate_uses_different_source_and_target_check_fields(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_asset_tc_vprod_order",
            "dep_tbls": json.dumps(["ods_r2_tc_vprod_order"]),
            "increment_field": "etl_create_time",
            "check_field": "",
        }
        ods_table_by_dest = {
            "ods_r2_tc_vprod_order": {
                "dest_tbl": "ods_r2_tc_vprod_order",
                "check_field": "",
                "columns": json.dumps(["id", "created_at", "order_no"]),
                "src_tbl": "r2_tc_vprod_order",
            }
        }

        result = module.build_count_rule_candidate("dwd", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["src_check_field"], "created_at")
        self.assertEqual(result["candidate"]["dest_check_field"], "etl_create_time")
        self.assertIn("WHERE created_at >=", result["candidate"]["src_sql"])
        self.assertIn("WHERE etl_create_time >=", result["candidate"]["dest_sql"])

    def test_infer_source_check_field_prefers_git_hint_before_target_increment_field(self):
        module = load_module()
        table = {
            "db": "dwd",
            "tbl": "dwd_user_activity_log",
            "dest_tbl": "dwd_user_activity_log",
            "src_tbl": "log_dp_request_record",
            "src_db": "log",
            "increment_field": "etl_create_time",
            "columns": None,
            "origin_check_field": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "dwd_user_activity_log.sql"
            sql_path.write_text(
                """
                insert overwrite dwd.dwd_user_activity_log
                select *
                from log.log_dp_request_record
                where request_time >= '${begin}'
                  and request_time < '${end}'
                """,
                encoding="utf-8",
            )

            check_field = module.infer_source_check_field(table, git_roots=[temp_dir])

        self.assertEqual(check_field, "request_time")

    def test_infer_source_check_field_ignores_origin_check_field_not_in_columns(self):
        module = load_module()
        table = {
            "db": "dwd",
            "tbl": "dwd_asset_tc_vprod_order",
            "dest_tbl": "dwd_asset_tc_vprod_order",
            "src_tbl": "ods_r2_tc_vprod_order",
            "src_db": "ods",
            "increment_field": "etl_create_time",
            "columns": json.dumps(["id", "created_at", "order_no"]),
            "origin_check_field": "etl_create_time",
            "origin_src_tbl": "r2_tc_vprod_order",
        }

        check_field = module.infer_source_check_field(table)

        self.assertEqual(check_field, "created_at")

    def test_infer_git_rule_hints_extracts_dep_table_and_check_field(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "dwd_user_member_log.sql"
            sql_path.write_text(
                """
                insert overwrite dwd.dwd_user_member_log
                select *
                from ods.ods_user_member_log
                where etl_create_time >= '${begin}'
                  and etl_create_time < '${end}'
                """,
                encoding="utf-8",
            )

            hints = module.infer_git_rule_hints(
                "dwd_user_member_log",
                git_roots=[temp_dir],
            )

        self.assertEqual(hints["dep_tbls"], ["ods.ods_user_member_log"])
        self.assertEqual(hints["check_field"], "etl_create_time")
        self.assertEqual(Path(hints["git_matches"][0]).name, "dwd_user_member_log.sql")

    def test_build_count_rule_candidate_marks_missing_dep_tbls_as_blocked(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_user_member_log",
            "dep_tbls": json.dumps([]),
            "increment_field": "etl_create_time",
            "check_field": "",
        }

        result = module.build_count_rule_candidate("dwd", table, {}, {})

        self.assertEqual(result["status"], "blocked")
        self.assertIn("dep_tbls", result["reason"])

    def test_build_count_rule_candidate_uses_ai_fallback_when_dep_tbls_missing(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_user_member_log",
            "dep_tbls": json.dumps([]),
            "increment_field": "",
            "check_field": "",
        }
        ai_candidate = {
            "name": "cnt",
            "desc": "总数",
            "src_db": "ods",
            "src_tbl": "ods_user_member_log",
            "dest_db": "dwd",
            "dest_tbl": "dwd_user_member_log",
            "src_sql": "select 1",
            "dest_sql": "select 2",
            "msg_template": "tpl",
            "src_check_field": "created_at",
            "dest_check_field": "etl_create_time",
            "ai_reason": "ai guessed it",
        }

        with mock.patch.object(module, "generate_rule_candidate_with_ai", return_value=ai_candidate):
            result = module.build_count_rule_candidate("dwd", table, {}, {})

        self.assertEqual(result["status"], "candidate")
        self.assertIn("AI 兜底生成", result["reason"])
        self.assertEqual(result["candidate"]["src_tbl"], "ods_user_member_log")

    def test_build_count_rule_candidate_uses_git_hints_when_metadata_missing(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_app_diversion_order",
            "dep_tbls": json.dumps([]),
            "increment_field": "",
            "check_field": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "dwd_app_diversion_order.sql"
            sql_path.write_text(
                """
                insert overwrite dwd.dwd_app_diversion_order
                select *
                from ods.ods_app_diversion_order
                where created_at >= '${begin}'
                  and created_at < '${end}'
                """,
                encoding="utf-8",
            )

            result = module.build_count_rule_candidate(
                "dwd",
                table,
                {},
                {},
                git_roots=[temp_dir],
            )

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["src_tbl"], "ods_app_diversion_order")
        self.assertEqual(result["candidate"]["check_field"], "created_at")
        self.assertTrue(result["candidate"]["git_matches"])

    def test_build_count_rule_candidate_marks_missing_check_field_as_blocked(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_user_member_log",
            "dep_tbls": json.dumps(["ods_user_member_log"]),
            "increment_field": "",
            "check_field": "",
        }

        result = module.build_count_rule_candidate("dwd", table, {}, {})

        self.assertEqual(result["status"], "blocked")
        self.assertIn("check_field", result["reason"])

    def test_build_exists_rule_candidate_generates_if_exists_rule(self):
        module = load_module()
        table = {"db": "ads", "tbl": "ads_collect_user_d"}

        result = module.build_exists_rule_candidate("ads", table, {})

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["rule_name"], "if_exists")
        self.assertIn("etl_create_time", result["candidate"]["dest_sql"])

    def test_scan_quality_rule_gaps_mirrors_database_scope(self):
        fake_cursor = FakeCursor(
            [
                [{"id": 1, "db": "dwd", "tbl": "dwd_user_member_log", "dep_tbls": json.dumps(["ods_user_member_log"]), "increment_field": "etl_create_time", "check_field": ""}],
                [],
                [],
            ]
        )
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))

        results = module.scan_quality_rule_gaps(databases=["dwd"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["database"], "dwd")
        self.assertEqual(results[0]["status"], "candidate")
        self.assertTrue(fake_conn.closed)

    def test_main_json_output_serializes_datetime_values(self):
        module = load_module()
        fake_results = [
            {
                "status": "candidate",
                "database": "dwd",
                "dest_tbl": "dwd_user_member_log",
                "rule_name": "cnt",
                "reason": "ok",
                "created_at": datetime(2026, 6, 4, 16, 0, 0),
            }
        ]

        with mock.patch.object(module, "scan_quality_rule_gaps", return_value=fake_results):
            stdout = StringIO()
            with mock.patch("sys.stdout", stdout):
                exit_code = module.main(["--json"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("2026-06-04T16:00:00", output)

    def test_apply_candidates_inserts_only_candidate_rules(self):
        fake_cursor = FakeCursor([[], []])
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))
        results = [
            {
                "status": "candidate",
                "candidate": {
                    "name": "cnt",
                    "desc": "总数",
                    "src_db": "ods",
                    "src_tbl": "ods_user_member_log",
                    "dest_db": "dwd",
                    "dest_tbl": "dwd_user_member_log",
                    "src_sql": "select 1",
                    "dest_sql": "select 2",
                    "msg_template": "tpl",
                },
            },
            {"status": "blocked"},
        ]

        applied = module.apply_candidates(results)

        self.assertEqual(applied, 1)
        self.assertTrue(fake_conn.committed)
        self.assertEqual(len(fake_cursor.executed), 1)
        insert_sql, params = fake_cursor.executed[0]
        self.assertIn("INSERT INTO wattrel_quality_setting", insert_sql)
        self.assertEqual(params[0], "cnt")
        self.assertEqual(params[5], "dwd_user_member_log")

    def test_validate_candidates_runs_explain_for_select_statements(self):
        fake_cursor = FakeCursor([[], []])
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))
        results = [
            {
                "status": "candidate",
                "candidate_key": "dwd::dwd.dwd_user_member_log::cnt",
                "candidate": {
                    "src_sql": "SELECT COUNT(*) as cnt FROM ods.ods_user_member_log WHERE etl_create_time >= '{begin}' AND etl_create_time < '{end}'",
                    "dest_sql": "SELECT COUNT(*) as cnt FROM dwd.dwd_user_member_log WHERE etl_create_time >= '{begin}' AND etl_create_time < '{end}'",
                },
            },
        ]

        validation = module.validate_candidates(results)

        self.assertEqual(len(validation["passed"]), 1)
        self.assertEqual(len(validation["failed"]), 0)
        self.assertTrue(fake_cursor.executed[0][0].startswith("EXPLAIN SELECT COUNT(*)"))

    def test_disable_auto_check_for_items_updates_exact_etl_table(self):
        fake_cursor = FakeCursor([[]])
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))

        updated = module.disable_auto_check_for_items(
            [{"database": "dwd", "dest_db": "dwd", "dest_tbl": "dwd_user_member_log"}]
        )

        self.assertEqual(updated, 1)
        sql, params = fake_cursor.executed[0]
        self.assertIn("UPDATE wattrel_etl_table_settings", sql)
        self.assertEqual(params, ("dwd", "dwd_user_member_log"))


if __name__ == "__main__":
    unittest.main()
