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
        module.QUALITY_RULE_VALIDATION_CONFIG["backend"] = "db"
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
            "increment_field": "created_at",
            "check_field": "",
        }
        ods_table_by_dest = {
            "ods_user_member_log": {
                "dest_tbl": "ods_user_member_log",
                "check_field": "",
                "columns": json.dumps(["id", "created_at", "other_field"]),
                "src_tbl": "user_member_log",
            }
        }

        result = module.build_count_rule_candidate("dwd", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["src_db"], "ods")
        self.assertEqual(result["candidate"]["src_tbl"], "ods_user_member_log")
        self.assertEqual(result["candidate"]["check_field"], "created_at")
        self.assertEqual(result["candidate"]["src_check_field"], "created_at")
        self.assertIn("SELECT COUNT(*)", result["candidate"]["src_sql"])

    def test_build_count_rule_candidate_blocks_when_check_fields_differ(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd",
            "tbl": "dwd_cst_pay_cost_detail",
            "dep_tbls": json.dumps(["ods_repay_cpop_income_item"]),
            "increment_field": "fee_finish_at",
            "check_field": "fee_finish_at",
            "dest_columns": ["fee_finish_at", "etl_create_time"],
        }
        ods_table_by_dest = {
            "ods_repay_cpop_income_item": {
                "dest_tbl": "ods_repay_cpop_income_item",
                "check_field": "",
                "columns": json.dumps(["id", "create_at", "order_no"]),
                "src_tbl": "repay_cpop_income_item",
            }
        }
        with mock.patch.object(module, "validate_candidate_sql") as mocked_validate:
            result = module.build_count_rule_candidate("dwd", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["validation_status"], "not_validated")
        self.assertIn("src_check_field 和 dest_check_field 必须一致", result["reason"])
        mocked_validate.assert_not_called()

    def test_build_count_rule_candidate_escalates_fast_path_when_dest_falls_back_to_etl_time(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd_sec",
            "tbl": "dwd_cst_pay_cost_detail",
            "dep_tbls": json.dumps(["ods_repay_cpop_income_item"]),
            "increment_field": "etl_create_time",
            "check_field": "etl_create_time",
        }
        ods_table_by_dest = {
            "ods_repay_cpop_income_item": {
                "dest_tbl": "ods_repay_cpop_income_item",
                "check_field": "",
                "columns": json.dumps(["id", "create_at", "order_no"]),
                "src_tbl": "repay_cpop_income_item",
            }
        }
        ai_candidate = {
            "name": "cnt",
            "src_db": "ods",
            "src_tbl": "ods_repay_cpop_income_item",
            "dest_db": "dwd_sec",
            "dest_tbl": "dwd_cst_pay_cost_detail",
            "src_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM ods.ods_repay_cpop_income_item WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
            "src_check_field": "create_at",
            "dest_check_field": "fee_finish_at",
            "ai_reason": "目标侧 etl 时间口径过弱，改用业务完成时间",
        }
        ai_meta = {"status": "ok", "reason": "", "git_matches": []}

        with mock.patch.object(module, "call_ai_candidate", return_value=(ai_candidate, ai_meta)) as mocked_ai:
            with mock.patch.object(
                module,
                "finalize_candidate_with_validation",
                return_value={"status": "candidate", "candidate": ai_candidate},
            ) as mocked_finalize:
                result = module.build_count_rule_candidate("dwd_sec", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "candidate")
        self.assertIn("目标侧 ETL 时间字段", mocked_ai.call_args.args[2])
        mocked_finalize.assert_called_once()

    def test_build_count_rule_candidate_keeps_ai_draft_sql_in_blocked_result(self):
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
        ai_meta = {
            "status": "ai_output_inconsistent_fields",
            "reason": "AI 生成的字段不一致: create_at != etl_create_time",
            "git_matches": ["/data/git/starrocks/workflow/ph/dwd/job.sql"],
            "draft_candidate": {
                "src_db": "ods",
                "src_tbl": "ods_r2_tc_vprod_order",
                "src_sql": "select count(*) from ods.ods_r2_tc_vprod_order where create_at >= '{begin}'",
                "dest_sql": "select count(*) from dwd.dwd_asset_tc_vprod_order where etl_create_time >= '{begin}'",
                "dest_check_field": "etl_create_time",
                "git_matches": ["/data/git/starrocks/workflow/ph/dwd/job.sql"],
            },
        }

        with mock.patch.object(module, "infer_source_check_field", return_value=None):
            with mock.patch.object(module, "call_ai_candidate", return_value=(None, ai_meta)):
                result = module.build_count_rule_candidate("dwd", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("AI状态=ai_output_inconsistent_fields", result["reason"])
        self.assertIn("create_at", result["src_sql"])
        self.assertIn("etl_create_time", result["dest_sql"])

    def test_build_validation_window_uses_rolling_last_24_hours(self):
        module = load_module()

        class FixedDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 6, 5, 14, 30, 45, 123456)

        with mock.patch.object(module, "datetime", FixedDatetime):
            begin, end = module.build_validation_window()

        self.assertEqual(begin, "2026-06-04 14:30:45")
        self.assertEqual(end, "2026-06-05 14:30:45")

    def test_validate_candidate_sql_returns_mismatched_when_values_differ(self):
        fake_cursor = FakeCursor([[{"cnt": 3}], [{"cnt": 5}]])
        module = load_module()
        candidate = {
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.a WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.b WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
        }

        result = module.validate_candidate_sql(fake_cursor, candidate)

        self.assertFalse(result["ok"])
        self.assertEqual(result["validation_status"], "mismatched")
        self.assertEqual(result["src_value"], 3)
        self.assertEqual(result["dest_value"], 5)

    def test_enrich_ai_schema_context_fetches_dest_columns_when_raw_value_is_stringified_empty_list(self):
        fake_cursor = FakeCursor([
            [("fee_finish_at", "datetime"), ("etl_create_time", "datetime")],
            [("dwd_cst_pay_cost_detail", "CREATE TABLE `dwd_cst_pay_cost_detail` (\n  `fee_finish_at` datetime,\n  `etl_create_time` datetime\n)")],
        ])
        module = load_module()

        enriched = module.enrich_ai_schema_context(
            {
                "db": "dwd_sec",
                "tbl": "dwd_cst_pay_cost_detail",
                "dest_db": "dwd_sec",
                "dest_tbl": "dwd_cst_pay_cost_detail",
                "dest_columns": "[]",
                "dest_ddl_summary": "",
            },
            cursor=fake_cursor,
        )

        self.assertEqual(enriched["dest_columns"], ["fee_finish_at", "etl_create_time"])
        self.assertEqual(enriched["dest_schema_status"], "ok")

    def test_extract_columns_from_ddl_summary_parses_backtick_columns(self):
        module = load_module()
        ddl = "CREATE TABLE `x` (\n  `fee_finish_at` datetime,\n  `etl_create_time` datetime,\n  `total_cost` decimal(38,8)\n)"
        self.assertEqual(
            module.extract_columns_from_ddl_summary(ddl),
            ["fee_finish_at", "etl_create_time", "total_cost"],
        )

    def test_validate_candidate_sql_uses_sr_gateway_backend_when_configured(self):
        module = load_module()
        module.QUALITY_RULE_VALIDATION_CONFIG["backend"] = "sr_gateway"
        module.QUALITY_RULE_VALIDATION_CONFIG["sr_base_url"] = "https://sr-box.kuainiu.io"
        module.QUALITY_RULE_VALIDATION_CONFIG["sr_token"] = "demo-token"
        module.QUALITY_RULE_FORM_CONFIG["country"] = "ph"
        candidate = {
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.a WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.b WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
            "country": "ph",
        }

        responses = [
            {"success": True, "data": {"rows": [{"cnt": 7}]}},
            {"success": True, "data": {"rows": [{"cnt": 7}]}},
        ]
        with mock.patch.object(module, "request_sr_gateway_json", side_effect=responses) as mocked:
            result = module.validate_candidate_sql(None, candidate)

        self.assertTrue(result["ok"])
        self.assertEqual(result["validation_status"], "matched")
        self.assertEqual(mocked.call_count, 2)

    def test_validate_candidate_sql_returns_validation_failed_when_sr_gateway_rejects(self):
        module = load_module()
        module.QUALITY_RULE_VALIDATION_CONFIG["backend"] = "sr_gateway"
        candidate = {
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.a WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.b WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
            "country": "ph",
        }

        with mock.patch.object(module, "request_sr_gateway_json", side_effect=RuntimeError("SR Gateway HTTP 403: denied")):
            result = module.validate_candidate_sql(None, candidate)

        self.assertFalse(result["ok"])
        self.assertEqual(result["validation_status"], "validation_failed")
        self.assertIn("SR Gateway HTTP 403", result["validation_error"])

    def test_validate_candidate_sql_syntax_uses_explain_for_select_queries(self):
        fake_cursor = FakeCursor([])
        module = load_module()
        candidate = {
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.a WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.b WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
        }

        result = module.validate_candidate_sql_syntax(fake_cursor, candidate)

        self.assertTrue(result["ok"])
        self.assertEqual(result["validation_status"], "syntax_ok")
        self.assertTrue(fake_cursor.executed[0][0].lstrip().startswith("EXPLAIN SELECT COUNT(*)"))
        self.assertTrue(fake_cursor.executed[1][0].lstrip().startswith("EXPLAIN SELECT COUNT(*)"))

    def test_validate_candidate_sql_syntax_returns_failed_when_gateway_rejects(self):
        module = load_module()
        module.QUALITY_RULE_VALIDATION_CONFIG["backend"] = "sr_gateway"
        candidate = {
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.a WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.b WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
            "country": "ph",
        }

        with mock.patch.object(module, "request_sr_gateway_json", side_effect=RuntimeError("SR Gateway HTTP 403: denied")):
            result = module.validate_candidate_sql_syntax(None, candidate)

        self.assertFalse(result["ok"])
        self.assertEqual(result["validation_status"], "syntax_failed")
        self.assertIn("SR Gateway HTTP 403", result["validation_error"])

    def test_validate_candidates_for_apply_uses_db_syntax_check_even_when_backend_is_sr_gateway(self):
        fake_cursor = FakeCursor([])
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))
        module.QUALITY_RULE_VALIDATION_CONFIG["backend"] = "sr_gateway"
        results = [
            {
                "status": "candidate",
                "candidate_key": "dwd::dwd.demo::cnt",
                "candidate": {
                    "src_sql": "SELECT COUNT(*) AS cnt FROM ods.demo WHERE created_at >= '{begin}' AND created_at < '{end}'",
                    "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd.demo WHERE created_at >= '{begin}' AND created_at < '{end}'",
                    "country": "ph",
                },
            }
        ]

        validation = module.validate_candidates_for_apply(results)

        self.assertEqual(len(validation["passed"]), 1)
        self.assertEqual(len(validation["failed"]), 0)
        self.assertTrue(fake_conn.closed)
        self.assertEqual(len(fake_cursor.executed), 2)
        self.assertTrue(fake_cursor.executed[0][0].lstrip().startswith("EXPLAIN SELECT COUNT(*)"))
        self.assertTrue(fake_cursor.executed[1][0].lstrip().startswith("EXPLAIN SELECT COUNT(*)"))

    def test_finalize_candidate_with_validation_retries_ai_once_on_mismatch(self):
        fake_cursor = FakeCursor([[{"cnt": 3}], [{"cnt": 5}], [{"cnt": 3}], [{"cnt": 3}]])
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))
        working_table = {
            "src_db": "ods",
            "src_tbl": "ods_repay_cpop_income_item",
            "dest_db": "dwd_sec",
            "dest_tbl": "dwd_cst_pay_cost_detail",
            "source_columns": ["create_at", "order_no"],
            "dest_columns": ["fee_finish_at", "etl_create_time", "order_no"],
        }
        candidate = {
            "name": "cnt",
            "src_db": "ods",
            "src_tbl": "ods_repay_cpop_income_item",
            "dest_db": "dwd_sec",
            "dest_tbl": "dwd_cst_pay_cost_detail",
            "src_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM ods.ods_repay_cpop_income_item WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "src_check_field": "create_at",
            "dest_check_field": "create_at",
            "ai_reason": "first",
            "git_matches": [],
        }
        retry_candidate = {
            **candidate,
            "dest_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "ai_reason": "retry fixed it",
        }

        with mock.patch.object(module, "call_ai_candidate", return_value=(retry_candidate, {"status": "ok", "reason": "", "git_matches": []})):
            result = module.finalize_candidate_with_validation(
                "dwd_sec",
                "dwd_cst_pay_cost_detail",
                "dwd_sec",
                candidate,
                working_table,
                git_roots=[],
                cursor=fake_cursor,
                base_reason="可自动生成规则",
            )

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["ai_retry_count"], 1)
        self.assertEqual(result["validation_status"], "matched")
        self.assertIn("AI 二次修正后通过真实校验", result["reason"])

    def test_finalize_candidate_with_validation_blocks_before_validation_when_fields_differ(self):
        module = load_module()
        candidate = {
            "name": "cnt",
            "src_db": "ods",
            "src_tbl": "ods_repay_cpop_income_item",
            "dest_db": "dwd_sec",
            "dest_tbl": "dwd_cst_pay_cost_detail",
            "src_sql": "SELECT COUNT(*) AS cnt FROM ods.ods_repay_cpop_income_item WHERE create_at >= '{begin}' AND create_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
            "src_check_field": "create_at",
            "dest_check_field": "fee_finish_at",
        }

        with mock.patch.object(module, "validate_candidate_sql") as mocked_validate:
            result = module.finalize_candidate_with_validation(
                "dwd_sec",
                "dwd_cst_pay_cost_detail",
                "dwd_sec",
                candidate,
                {},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["validation_status"], "not_validated")
        self.assertIn("src_check_field 和 dest_check_field 必须一致", result["reason"])
        mocked_validate.assert_not_called()

    def test_build_count_rule_candidate_does_not_accept_cross_table_generic_source_etl_field(self):
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
                "check_field": "etl_create_time",
                "columns": json.dumps(["id", "order_no", "etl_create_time"]),
                "src_tbl": "r2_tc_vprod_order",
            }
        }

        with mock.patch.object(module, "generate_rule_candidate_with_ai", return_value=None):
            result = module.build_count_rule_candidate("dwd", table, {}, ods_table_by_dest)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("无法推断 src_check_field/dest_check_field", result["reason"])

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

    def test_infer_source_check_field_ignores_generic_git_etl_field_without_source_columns(self):
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
                where etl_create_time >= '${begin}'
                  and etl_create_time < '${end}'
                """,
                encoding="utf-8",
            )

            check_field = module.infer_source_check_field(table, git_roots=[temp_dir])

        self.assertIsNone(check_field)

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

    def test_infer_source_check_field_ignores_unverified_origin_etl_field_without_columns(self):
        module = load_module()
        table = {
            "db": "dwd",
            "tbl": "dwd_asset_tc_vprod_order",
            "dest_tbl": "dwd_asset_tc_vprod_order",
            "src_tbl": "ods_r2_tc_vprod_order",
            "src_db": "ods",
            "increment_field": "etl_create_time",
            "columns": None,
            "origin_check_field": "etl_create_time",
            "origin_src_tbl": "r2_tc_vprod_order",
        }

        check_field = module.infer_source_check_field(table)

        self.assertIsNone(check_field)

    def test_infer_source_check_field_returns_none_for_unverified_etl_increment_field(self):
        module = load_module()
        table = {
            "db": "dwd",
            "tbl": "dwd_asset_tc_vprod_order",
            "dest_tbl": "dwd_asset_tc_vprod_order",
            "src_tbl": "ods_r2_tc_vprod_order",
            "src_db": "ods",
            "increment_field": "etl_create_time",
            "columns": None,
            "origin_check_field": None,
        }

        check_field = module.infer_source_check_field(table)

        self.assertIsNone(check_field)

    def test_infer_source_check_field_allows_etl_increment_field_when_present_in_source_columns(self):
        module = load_module()
        table = {
            "db": "dwd",
            "tbl": "dwd_x",
            "dest_tbl": "dwd_x",
            "src_tbl": "ods_x",
            "src_db": "ods",
            "increment_field": "etl_create_time",
            "columns": json.dumps(["id", "etl_create_time"]),
            "origin_check_field": None,
        }

        check_field = module.infer_source_check_field(table)

        self.assertEqual(check_field, "etl_create_time")

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

    def test_parse_git_roots_prefers_country_specific_starrocks_workflow_directory(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_root = Path(temp_dir) / "starrocks" / "workflow" / "ph"
            workflow_root.mkdir(parents=True)
            backup_root = Path(temp_dir) / "starrocks.bk" / "workflow"
            backup_root.mkdir(parents=True)
            unused_root = Path(temp_dir) / "unused"

            with mock.patch.object(module, "DEFAULT_GIT_SCAN_ROOTS", (str(unused_root),)):
                with mock.patch.dict(module.os.environ, {"QUALITY_RULE_FORM_COUNTRY": "ph"}, clear=False):
                    with mock.patch.object(module.os.path, "isdir", side_effect=lambda path: Path(path).is_dir()):
                        with mock.patch.object(
                            module,
                            "default_git_scan_roots",
                            wraps=lambda: [
                                path
                                for path in (
                                    str(workflow_root),
                                    str(Path(temp_dir) / "starrocks" / "workflow"),
                                    str(backup_root),
                                )
                                if Path(path).is_dir()
                            ],
                        ):
                            roots = module.default_git_scan_roots()

        self.assertEqual(roots[0], str(workflow_root))

    def test_infer_git_rule_hints_can_fallback_to_content_scan_when_filename_does_not_match(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "job_001.sql"
            sql_path.write_text(
                """
                insert overwrite dwd.dwd_asset_tc_vprod_order
                select *
                from ods.ods_r2_tc_vprod_order
                where created_at >= '${begin}'
                  and created_at < '${end}'
                """,
                encoding="utf-8",
            )

            hints = module.infer_git_rule_hints("dwd_asset_tc_vprod_order", git_roots=[temp_dir])

        self.assertEqual(hints["dep_tbls"], ["ods.ods_r2_tc_vprod_order"])
        self.assertEqual(hints["check_field"], "created_at")
        self.assertEqual(Path(hints["git_matches"][0]).name, "job_001.sql")

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
            "dest_check_field": "created_at",
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

    def test_build_exists_rule_candidate_prefers_git_hint_field_for_ads(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "ads_collect_user_d.sql"
            sql_path.write_text(
                "select count(*) from ads.ads_collect_user_d "
                "where create_at >= '{begin}' and create_at < '{end}'",
                encoding="utf-8",
            )
            table = {"db": "ads", "tbl": "ads_collect_user_d"}

            result = module.build_exists_rule_candidate("ads", table, {}, git_roots=[temp_dir])

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["rule_name"], "if_exists")
        self.assertIn("create_at", result["candidate"]["dest_sql"])
        self.assertEqual(result["candidate"]["check_field"], "create_at")

    def test_build_exists_rule_candidate_falls_back_to_ai_when_only_etl_timestamp_available(self):
        module = load_module()
        table = {
            "db": "ads_sec",
            "tbl": "ads_3602_asset_flow_d",
            "increment_field": "etl_create_time",
            "check_field": "",
            "columns": json.dumps([]),
        }
        ai_candidate = {
            "name": "cnt",
            "src_db": "dwd",
            "src_tbl": "dwd_asset_flow_src",
            "dest_db": "ads_sec",
            "dest_tbl": "ads_3602_asset_flow_d",
            "src_sql": "SELECT COUNT(*) AS cnt FROM dwd.dwd_asset_flow_src WHERE updated_at >= '{begin}' AND updated_at < '{end}'",
            "dest_sql": "SELECT COUNT(*) AS cnt FROM ads_sec.ads_3602_asset_flow_d WHERE updated_at >= '{begin}' AND updated_at < '{end}'",
            "src_check_field": "updated_at",
            "dest_check_field": "updated_at",
            "ai_reason": "使用业务更新时间替代 etl_create_time",
        }
        ai_meta = {"status": "ok", "reason": "", "git_matches": []}

        with mock.patch.object(module, "call_ai_candidate", return_value=(ai_candidate, ai_meta)) as mocked_ai:
            with mock.patch.object(
                module,
                "finalize_candidate_with_validation",
                return_value={"status": "candidate", "candidate": ai_candidate},
            ) as mocked_finalize:
                result = module.build_exists_rule_candidate("ads_sec", table, {}, cursor=mock.MagicMock())

        self.assertEqual(result["status"], "candidate")
        self.assertIn("已阻止使用 etl_create_time 兜底", mocked_ai.call_args.args[2])
        mocked_finalize.assert_called_once()

    def test_build_exists_rule_candidate_returns_blocked_when_ai_also_fails(self):
        module = load_module()
        table = {
            "db": "ads_sec",
            "tbl": "ads_3602_asset_flow_d",
            "increment_field": "etl_create_time",
            "check_field": "",
            "columns": json.dumps([]),
        }
        ai_meta = {"status": "failed", "reason": "no usable sql", "git_matches": []}

        with mock.patch.object(module, "call_ai_candidate", return_value=(None, ai_meta)):
            result = module.build_exists_rule_candidate("ads_sec", table, {}, cursor=mock.MagicMock())

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["rule_name"], "if_exists")
        self.assertIn("AI 未能补出可用 SQL", result["reason"])

    def test_build_count_rule_candidate_uses_requested_metric_field_override(self):
        module = load_module()
        table = {
            "id": 1,
            "db": "dwd_sec",
            "tbl": "dwd_cst_pay_cost_detail",
            "dep_tbls": json.dumps(["ods_paysvr_fee"]),
            "increment_field": "fee_finish_at",
            "check_field": "fee_finish_at",
        }
        ods_table_by_dest = {
            "ods_paysvr_fee": {
                "dest_tbl": "ods_paysvr_fee",
                "check_field": "fee_finish_at",
                "columns": json.dumps(["fee_finish_at", "fee_amount"]),
                "src_tbl": "paysvr_fee",
            }
        }

        with mock.patch.object(module, "build_requested_metric_candidate_with_ai") as mocked_builder:
            mocked_builder.return_value = {
                "status": "candidate",
                "rule_name": "cnt",
                "dest_tbl": "dwd_cst_pay_cost_detail",
                "dest_db": "dwd_sec",
                "reason": "确认表指定字段 total_cost，按指定字段生成规则",
                "candidate": {"requested_metric_field": "total_cost"},
            }
            result = module.build_count_rule_candidate(
                "dwd_sec",
                table,
                {},
                ods_table_by_dest,
                requested_metric_field="total_cost",
            )

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["requested_metric_field"], "total_cost")
        self.assertEqual(mocked_builder.call_args.args[4], "total_cost")

    def test_scan_quality_rule_gaps_mirrors_database_scope(self):
        fake_cursor = FakeCursor(
            [
                [{"id": 1, "db": "dwd", "tbl": "dwd_user_member_log", "dep_tbls": json.dumps(["ods_user_member_log"]), "increment_field": "created_at", "check_field": ""}],
                [],
                [{"dest_tbl": "ods_user_member_log", "check_field": "", "columns": json.dumps(["id", "created_at"]), "src_tbl": "user_member_log"}],
            ]
        )
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))

        results = module.scan_quality_rule_gaps(databases=["dwd"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["database"], "dwd")
        self.assertEqual(results[0]["status"], "candidate")
        self.assertTrue(fake_conn.closed)

    def test_list_pending_generation_tables_excludes_existing_rules(self):
        fake_cursor = FakeCursor(
            [
                [
                    {"id": 1, "db": "dwd", "tbl": "dwd_has_rule", "monitor_level": 3, "is_auto_check": 1},
                    {"id": 2, "db": "dwd", "tbl": "dwd_needs_rule", "monitor_level": 3, "is_auto_check": 1},
                ],
                [
                    {"dest_db": "dwd", "dest_tbl": "dwd_has_rule", "name": "cnt"},
                ],
            ]
        )
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))

        results = module.list_pending_generation_tables(databases=["dwd"])

        self.assertEqual(
            results,
            [
                {
                    "database": "dwd",
                    "tbl": "dwd_needs_rule",
                    "dest_db": "dwd",
                    "rule_name": "cnt",
                    "status": "pending_generation",
                    "reason": "缺少 cnt 规则，待进入自动生成",
                    "monitor_level": 3,
                }
            ],
        )

    def test_list_pending_generation_tables_skips_ineligible_ods_tables(self):
        fake_cursor = FakeCursor(
            [
                [
                    {"dest_db": "ods", "dest_tbl": "ods_no_pk", "pk": None, "dest_tbl_partition_field": None, "monitor_level": 1},
                    {"dest_db": "ods", "dest_tbl": "ods_partitioned", "pk": "id", "dest_tbl_partition_field": "dt", "monitor_level": 1},
                    {"dest_db": "ods", "dest_tbl": "ods_ok", "pk": "id", "dest_tbl_partition_field": None, "monitor_level": 2},
                ],
                [],
            ]
        )
        fake_conn = FakeConnection(fake_cursor)
        module = load_module(fake_get_db_connection=mock.MagicMock(return_value=fake_conn))

        results = module.list_pending_generation_tables(databases=["ods"])

        self.assertEqual(
            results,
            [
                {
                    "database": "ods",
                    "tbl": "ods_ok",
                    "dest_db": "ods",
                    "rule_name": "cnt",
                    "status": "pending_generation",
                    "reason": "缺少 cnt 规则，待进入自动生成",
                    "monitor_level": 2,
                }
            ],
        )

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

    def test_validate_candidates_executes_real_select_statements(self):
        fake_cursor = FakeCursor([[{"cnt": 8}], [{"cnt": 8}]])
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
        self.assertTrue(fake_cursor.executed[0][0].startswith("SELECT COUNT(*)"))

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
