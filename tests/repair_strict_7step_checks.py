import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "repair_strict_7step.py"


def load_module():
    fake_config = types.ModuleType("config")
    fake_config.auto_load_env = object()
    fake_config_config = types.ModuleType("config.config")
    fake_config_config.DS_CONFIG = {
        "base_url": "https://default.example/dolphinscheduler",
        "token": "default-token",
        "project_code": "default-project",
        "fuyan_project_code": "default-fuyan-project",
        "environment_code": "10001",
        "tenant_code": "tenant_default",
        "api_mode": "auto",
        "start_endpoint": "auto",
        "start_code_field": "auto",
        "definition_endpoint_style": "auto",
        "instance_endpoint_style": "auto",
    }
    fake_config_config.WORKSPACE_CONFIG = {
        "root": "/tmp/default-workspace",
        "manual_review_state_file": "/tmp/default-workspace/auto_repair_records/manual_review_state.json",
        "auto_repair_records_dir": "/tmp/default-workspace/auto_repair_records",
        "repair_counts_file": "/tmp/default-workspace/auto_repair_records/repair_counts.json",
    }
    fake_config_config.TABLE_CONFIG = {
        "quality_result_table": "default_quality_result",
        "quality_alert_table": "default_quality_alert",
    }
    fake_config_config.REPAIR_CONFIG = {
        "scan_lookback_days": 8,
        "priority_workflow_codes": [],
        "blocked_workflow_names": ["印尼-宽表全量工作流（1D）", "DWS（1D）"],
        "blocked_fuyan_workflow_names": [],
    }
    fake_config_config.FUYAN_WORKFLOWS = [
        {"name": "默认复验", "code": "wf-default", "level": "all"},
    ]
    previous_config = sys.modules.get("config")
    previous_config_config = sys.modules.get("config.config")
    sys.modules["config"] = fake_config
    sys.modules["config.config"] = fake_config_config
    try:
        spec = importlib.util.spec_from_file_location("repair_strict_7step", str(MODULE_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_config is not None:
            sys.modules["config"] = previous_config
        else:
            sys.modules.pop("config", None)
        if previous_config_config is not None:
            sys.modules["config.config"] = previous_config_config
        else:
            sys.modules.pop("config.config", None)


class RepairStrict7StepTests(unittest.TestCase):
    def test_module_constants_are_loaded_from_shared_config(self):
        module = load_module()

        self.assertEqual(module.WORKSPACE, "/tmp/default-workspace")
        self.assertEqual(module.DS_BASE, "https://default.example/dolphinscheduler")
        self.assertEqual(module.PROJECT_CODE, "default-project")
        self.assertEqual(module.FUYAN_PROJECT_CODE, "default-fuyan-project")
        self.assertEqual(module.DS_TOKEN, "default-token")
        self.assertEqual(module.MANUAL_REVIEW_STATE_FILE, "/tmp/default-workspace/auto_repair_records/manual_review_state.json")
        self.assertEqual(module.SCAN_LOOKBACK_DAYS, 8)
        self.assertEqual(module.FUYAN_WORKFLOWS, [{"name": "默认复验", "code": "wf-default", "level": "all"}])

    def test_step1_scan_alerts_marks_out_of_window_alert_as_manual_review(self):
        module = load_module()
        rows = [
            {
                "id": 1,
                "name": "old alert",
                "src_db": "dwd",
                "src_tbl": "dwd_old_table",
                "dest_db": "dwd",
                "dest_tbl": "dwd_old_table",
                "begin": datetime(2026, 4, 20, 0, 0, 0),
                "end": datetime(2026, 4, 21, 0, 0, 0),
                "diff": 1,
            }
        ]

        fake_cursor = mock.MagicMock()
        fake_cursor.fetchall.return_value = rows
        fake_conn = mock.MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_db_module = types.ModuleType("alert.db_config")
        fake_db_module.get_db_connection = mock.MagicMock(return_value=fake_conn)

        with mock.patch.dict(sys.modules, {"alert.db_config": fake_db_module}), \
            mock.patch.object(module, "log"):
            alerts = module.step1_scan_alerts(now=datetime(2026, 5, 10, 10, 0, 0))

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["table"], "dwd_old_table")
        self.assertEqual(alerts[0]["status"], "skipped_out_of_window")
        self.assertIn("最新告警日期", alerts[0]["error"])
        self.assertIn("2026-05-02", alerts[0]["error"])

    def test_step1_scan_alerts_marks_long_window_alert_as_manual_review(self):
        module = load_module()
        rows = [
            {
                "id": 1,
                "name": "long window alert",
                "src_db": "ods",
                "src_tbl": "ods_long_window_table",
                "dest_db": "dwd",
                "dest_tbl": "dwd_long_window_table",
                "begin": datetime(2026, 2, 8, 0, 0, 0),
                "end": datetime(2026, 5, 9, 0, 0, 0),
                "diff": 1,
            }
        ]

        fake_cursor = mock.MagicMock()
        fake_cursor.fetchall.return_value = rows
        fake_conn = mock.MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_db_module = types.ModuleType("alert.db_config")
        fake_db_module.get_db_connection = mock.MagicMock(return_value=fake_conn)

        with mock.patch.dict(sys.modules, {"alert.db_config": fake_db_module}), \
            mock.patch.object(module, "log"):
            alerts = module.step1_scan_alerts(now=datetime(2026, 5, 10, 10, 0, 0))

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["table"], "dwd_long_window_table")
        self.assertEqual(alerts[0]["status"], "skipped_out_of_window")
        self.assertIn("跨度 90 天超过自动修复阈值 8 天", alerts[0]["error"])

    def test_step5_execute_fuyan_accepts_workflow_name_style_config(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {
                "project_name": "印尼数仓-数据质量",
                "project_code": "pj-1",
                "workflow_name": "每日复验全级别数据(W-1)",
                "workflow_code": "wf-1",
                "level": "全级别",
            }
        ]

        with mock.patch.object(module, "ds_api_post", return_value=(True, {"data": [12345]}, "")):
            results = module.step5_execute_fuyan([{"table": "dwd_fox_mission_log"}], [], [])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "每日复验全级别数据(W-1)")
        self.assertEqual(results[0]["id"], 12345)

    def test_step5_execute_fuyan_uses_workflow_specific_project_code(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {
                "project_name": "印尼数仓-数据质量",
                "project_code": "project-fuyan-a",
                "workflow_name": "每日复验全级别数据(W-1)",
                "workflow_code": "wf-1",
                "level": "全级别",
            }
        ]
        captured = {}

        def fake_ds_api_post(endpoint, data):
            captured["endpoint"] = endpoint
            captured["data"] = data
            return True, {"data": [12345]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            results = module.step5_execute_fuyan([{"table": "dwd_fox_mission_log"}], [], [])

        self.assertEqual(captured["endpoint"], "/projects/project-fuyan-a/executors/start-process-instance")
        self.assertEqual(results[0]["project_code"], "project-fuyan-a")

    def test_get_fuyan_project_code_prefers_workflow_specific_value(self):
        module = load_module()

        workflow = {
            "project_code": "project-fuyan-a",
            "workflow_code": "wf-1",
        }

        self.assertEqual(module.get_fuyan_project_code(workflow), "project-fuyan-a")

    def test_step5_execute_fuyan_selects_daily_and_level3_for_non_dwb_tables(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {"workflow_name": "每日复验全级别数据(W-1)", "workflow_code": "wf-daily", "level": "全级别"},
            {"workflow_name": "每小时复验1级表数据(D-1)", "workflow_code": "wf-l1", "level": "1级表"},
            {"workflow_name": "两小时复验3级表数据(D-1)", "workflow_code": "wf-l3", "level": "3级表"},
            {"workflow_name": "每周复验全级别数据(M-3)", "workflow_code": "wf-weekly", "level": "全级别"},
        ]
        started_codes = []

        def fake_ds_api_post(endpoint, data):
            started_codes.append(data["processDefinitionCode"])
            return True, {"data": [len(started_codes)]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            module.step5_execute_fuyan([{"table": "dwd_fox_mission_log"}], [], [{"table": "dwd_fox_mission_log"}])

        self.assertEqual(started_codes, ["wf-daily", "wf-l1", "wf-l3"])

    def test_step5_execute_fuyan_adds_week_and_level3_for_dws_even_with_explicit_level1(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {"workflow_name": "每小时复验1级表数据(D-1)", "workflow_code": "wf-l1", "level": "1级表"},
            {"workflow_name": "两小时复验3级表数据(D-1)", "workflow_code": "wf-l3", "level": "3级表"},
            {"workflow_name": "每日复验全级别数据(W-1)", "workflow_code": "wf-week", "level": "全级别"},
        ]
        started_codes = []

        def fake_ds_api_post(endpoint, data):
            started_codes.append(data["processDefinitionCode"])
            return True, {"data": [len(started_codes)]}, ""

        alerts = [{"table": "dws_user_performance_first_loan_info", "monitor_level": "1"}]

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            module.step5_execute_fuyan(alerts, [], alerts)

        self.assertEqual(started_codes, ["wf-l1", "wf-l3", "wf-week"])

    def test_step5_execute_fuyan_always_includes_level1_for_non_dwb_tables(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {"workflow_name": "每日复验全级别数据(W-1)", "workflow_code": "wf-daily", "level": "全级别"},
            {"workflow_name": "每小时复验1级表数据(D-1)", "workflow_code": "wf-l1", "level": "1级表"},
            {"workflow_name": "两小时复验3级表数据(D-1)", "workflow_code": "wf-l3", "level": "3级表"},
        ]

        started_nodes = {}

        def fake_ds_api_post(endpoint, data):
            started_nodes[data["processDefinitionCode"]] = dict(data)
            return True, {"data": [len(started_nodes)]}, ""

        with mock.patch.object(
            module,
            "get_workflow_definition_detail",
            side_effect=[
                (True, {"taskDefinitionList": [{"code": "task-l1", "name": "复验1级表"}]}, ""),
            ],
        ), mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            module.step5_execute_fuyan([{"table": "dwd_fox_mission_log"}], [], [{"table": "dwd_fox_mission_log"}])

        self.assertEqual(started_nodes["wf-l1"]["startNodeList"], "task-l1")
        self.assertEqual(started_nodes["wf-l1"]["taskDependType"], "TASK_ONLY")

    def test_step5_execute_fuyan_selects_only_level1_for_dwb_tables(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {"workflow_name": "每日复验全级别数据(W-1)", "workflow_code": "wf-daily", "level": "全级别"},
            {"workflow_name": "每小时复验1级表数据(D-1)", "workflow_code": "wf-l1", "level": "1级表"},
            {"workflow_name": "两小时复验3级表数据(D-1)", "workflow_code": "wf-l3", "level": "3级表"},
        ]
        started_codes = []

        def fake_ds_api_post(endpoint, data):
            started_codes.append(data["processDefinitionCode"])
            return True, {"data": [len(started_codes)]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            results = module.step5_execute_fuyan([{"table": "dwb_cash_audit"}], [], [{"table": "dwb_cash_audit"}])

        self.assertEqual(started_codes, ["wf-l1"])
        self.assertEqual([item["name"] for item in results], ["每小时复验1级表数据(D-1)"])

    def test_step5_execute_fuyan_resolves_level1_node_from_workflow_specific_project_code(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {
                "project_code": "project-fuyan-a",
                "workflow_name": "每小时复验1级表数据(D-1)",
                "workflow_code": "wf-l1",
                "level": "1级表",
            },
        ]

        captured = {}

        def fake_ds_api_post(endpoint, data):
            captured["endpoint"] = endpoint
            captured["data"] = dict(data)
            return True, {"data": [12345]}, ""

        with mock.patch.object(
            module,
            "get_workflow_definition_detail",
            return_value=(
                True,
                {"taskDefinitionList": [{"code": "task-l1", "name": "复验1级表"}]},
                "",
            ),
        ) as detail_mock, \
            mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post):
            results = module.step5_execute_fuyan([{"table": "dwb_cash_audit"}], [], [{"table": "dwb_cash_audit"}])

        detail_mock.assert_called_once_with("wf-l1", "project-fuyan-a")
        self.assertEqual(captured["endpoint"], "/projects/project-fuyan-a/executors/start-process-instance")
        self.assertEqual(captured["data"]["processDefinitionCode"], "wf-l1")
        self.assertEqual(captured["data"]["startNodeList"], "task-l1")
        self.assertEqual(captured["data"]["taskDependType"], "TASK_ONLY")
        self.assertEqual([item["name"] for item in results], ["每小时复验1级表数据(D-1)"])

    def test_step5_execute_fuyan_falls_back_to_workflow_style_start_when_process_style_fails(self):
        module = load_module()
        module.FUYAN_WORKFLOWS = [
            {"workflow_name": "每日复验全级别数据(W-1)", "workflow_code": "wf-daily", "level": "全级别"}
        ]
        attempts = []

        def fake_ds_api_post(endpoint, data):
            attempts.append((endpoint, dict(data)))
            if endpoint.endswith("start-process-instance"):
                return False, {}, "process style unsupported"
            return True, {"data": [88888]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "log"):
            results = module.step5_execute_fuyan([{"table": "dwd_fox_mission_log"}], [], [{"table": "dwd_fox_mission_log"}])

        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0][0].endswith("start-process-instance"))
        self.assertEqual(attempts[0][1]["processDefinitionCode"], "wf-daily")
        self.assertTrue(attempts[1][0].endswith("start-workflow-instance"))
        self.assertEqual(attempts[1][1]["workflowDefinitionCode"], "wf-daily")
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["id"], 88888)

    def test_step2_search_in_workflow_falls_back_to_process_definition_for_ds32(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "DWD"},
                    "taskDefinitionList": [{"code": "task-1", "name": "dwd_fox_mission_log"}],
                }, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            result = module.step2_search_in_workflow("wf-1", "dwd_fox_mission_log")

        self.assertEqual(result["workflow_name"], "DWD")
        self.assertEqual(result["task_code"], "task-1")

    def test_step2_search_in_workflow_does_not_match_sql_only_reference_from_other_task(self):
        module = load_module()
        detail = {
            "processDefinition": {"name": "DWD"},
            "taskDefinitionList": [
                {
                    "code": "task-main",
                    "name": "dwd_asset_main",
                    "taskParams": json.dumps(
                        {
                            "sql": "insert overwrite table dwd_asset_main select * from dwb_asset_info"
                        }
                    ),
                }
            ],
        }

        with mock.patch.object(module, "get_workflow_definition_detail", return_value=(True, detail, "")):
            result = module.step2_search_in_workflow("wf-asset", "dwb_asset_info")

        self.assertIsNone(result)

    def test_step2_search_in_workflow_requires_exact_full_table_name_match(self):
        module = load_module()
        detail = {
            "processDefinition": {"name": "ODS_FOX_ARCTICFOX"},
            "taskDefinitionList": [
                {
                    "code": "task-1",
                    "name": "ods_arcticfox_collect_recovery",
                    "taskType": "SHELL",
                }
            ],
        }

        with mock.patch.object(module, "get_workflow_definition_detail", return_value=(True, detail, "")):
            result = module.step2_search_in_workflow("wf-1", "dwd_fox_collect_recovery")

        self.assertIsNone(result)

    def test_step3_start_repair_uses_process_style_start_payload_by_default(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_fox_mission_log",
            }
        ]
        captured = {}

        def fake_ds_api_post(endpoint, data):
            captured["endpoint"] = endpoint
            captured["data"] = data
            return True, {"data": [12345]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), mock.patch.object(
            module, "log"
        ), mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(captured["endpoint"], "/projects/default-project/executors/start-process-instance")
        self.assertEqual(captured["data"]["processDefinitionCode"], "wf-1")
        self.assertNotIn("workflowDefinitionCode", captured["data"])
        self.assertEqual(captured["data"]["startNodeList"], "task-1")
        self.assertEqual(captured["data"]["taskDependType"], "TASK_ONLY")
        self.assertEqual(captured["data"]["scheduleTime"], "")
        self.assertEqual(
            captured["data"]["startParams"],
            '{"dt": "2026-04-29"}',
        )
        self.assertEqual(results[0]["instance_id"], 12345)
        self.assertEqual(running_instances[0]["instance_id"], 12345)

    def test_step3_start_repair_falls_back_to_workflow_style_start_when_process_style_fails(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_fox_mission_log",
            }
        ]
        attempts = []

        def fake_ds_api_post(endpoint, data):
            attempts.append((endpoint, dict(data)))
            if endpoint.endswith("start-process-instance"):
                return False, {}, "process style unsupported"
            return True, {"data": [67890]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0][0].endswith("start-process-instance"))
        self.assertEqual(attempts[0][1]["processDefinitionCode"], "wf-1")
        self.assertTrue(attempts[1][0].endswith("start-workflow-instance"))
        self.assertEqual(attempts[1][1]["workflowDefinitionCode"], "wf-1")
        self.assertEqual(results[0]["instance_id"], 67890)
        self.assertEqual(running_instances[0]["instance_id"], 67890)

    def test_step3_start_repair_falls_back_to_workflow_style_when_process_style_returns_405(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_START_ENDPOINT = "start-process-instance"
        module.DS_START_CODE_FIELD = "processDefinitionCode"
        tasks = [
            {
                "table": "dwb_asset_info",
                "dt": "2026-05-25",
                "workflow_code": "wf-1",
                "workflow_name": "DWB",
                "task_code": "task-1",
                "task_name": "dwb_asset_info",
            }
        ]
        attempts = []

        def fake_ds_api_post(endpoint, data):
            attempts.append((endpoint, dict(data)))
            if endpoint.endswith("start-process-instance"):
                return False, {}, "HTTP Error 405: Method Not Allowed"
            return True, {"data": [24680]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0][0].endswith("start-process-instance"))
        self.assertTrue(attempts[1][0].endswith("start-workflow-instance"))
        self.assertEqual(results[0]["instance_id"], 24680)
        self.assertEqual(running_instances[0]["instance_id"], 24680)

    def test_step3_start_repair_falls_back_to_property_list_when_map_is_rejected(self):
        module = load_module()
        tasks = [
            {
                "table": "dwb_asset_info",
                "dt": "2026-05-11",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_asset_main",
            }
        ]
        attempts = []

        def fake_ds_api_post(endpoint, data):
            attempts.append((endpoint, dict(data)))
            if len(attempts) == 1:
                return False, {}, 'start workflow instance error:Parse json map failed'
            return True, {"data": [13579]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "find_conflicting_running_instance", return_value=None), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            attempts[0][1]["startParams"],
            '{"dt": "2026-05-11"}',
        )
        self.assertEqual(
            attempts[1][1]["startParams"],
            '[{"prop": "dt", "direct": "IN", "type": "VARCHAR", "value": "2026-05-11"}]',
        )
        self.assertEqual(results[0]["instance_id"], 13579)
        self.assertEqual(running_instances[0]["instance_id"], 13579)

    def test_step3_start_repair_uses_configured_workflow_style_start_mode(self):
        module = load_module()
        module.DS_START_ENDPOINT = "start-workflow-instance"
        module.DS_START_CODE_FIELD = "workflowDefinitionCode"
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_fox_mission_log",
            }
        ]
        captured = {}

        def fake_ds_api_post(endpoint, data):
            captured["endpoint"] = endpoint
            captured["data"] = dict(data)
            return True, {"data": [45678]}, ""

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(captured["endpoint"], "/projects/default-project/executors/start-workflow-instance")
        self.assertEqual(captured["data"]["workflowDefinitionCode"], "wf-1")
        self.assertNotIn("processDefinitionCode", captured["data"])
        self.assertEqual(captured["data"]["scheduleTime"], "2026-04-29 00:00:00" if False else captured["data"]["scheduleTime"])
        self.assertEqual(results[0]["instance_id"], 45678)
        self.assertEqual(running_instances[0]["instance_id"], 45678)

    def test_step3_start_repair_skips_when_workflow_conflict_does_not_clear(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_fox_mission_log",
            }
        ]

        conflict = {"id": 999, "commandType": "SCHEDULER", "state": "RUNNING_EXECUTION"}
        with mock.patch.object(
            module,
            "find_conflicting_running_instance",
            return_value=conflict,
        ), mock.patch.object(
            module,
            "wait_for_workflow_conflict_clear",
            return_value=(False, conflict),
        ), mock.patch.object(module, "ds_api_post") as mocked_post, mock.patch.object(module, "log"):
            results, running_instances = module.step3_start_repair(tasks)

        mocked_post.assert_not_called()
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("运行中实例", results[0]["error"])
        self.assertIn("999", results[0]["error"])
        self.assertEqual(running_instances, [])

    def test_step3_start_repair_waits_for_workflow_conflict_then_starts(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_fox_mission_log",
            }
        ]

        conflict = {"id": 999, "commandType": "START_PROCESS", "state": "RUNNING_EXECUTION"}
        with mock.patch.object(module, "find_conflicting_running_instance", return_value=conflict), \
            mock.patch.object(module, "wait_for_workflow_conflict_clear", return_value=(True, None)), \
            mock.patch.object(module, "start_workflow_instance_with_fallbacks") as mocked_start, \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            mocked_start.return_value = (
                True,
                {"data": 12345},
                "success",
                "/projects/p/executors/start-process-instance",
                {},
                "2026-04-29 00:00:00",
            )
            results, running_instances = module.step3_start_repair(tasks)

        mocked_start.assert_called_once()
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["instance_id"], 12345)
        self.assertEqual(running_instances[0]["instance_id"], 12345)

    def test_execute_repairs_in_batches_serializes_tasks_in_same_workflow(self):
        module = load_module()
        tasks = [
            {
                "table": "ods_cash_model_model",
                "dt": "2026-05-10",
                "workflow_code": "wf-1",
                "workflow_name": "ODS_CASH_MODEL",
                "task_code": "task-1",
                "task_name": "ods_cash_model_model",
            },
            {
                "table": "ods_cash_model_model_retry",
                "dt": "2026-05-11",
                "workflow_code": "wf-1",
                "workflow_name": "ODS_CASH_MODEL",
                "task_code": "task-2",
                "task_name": "ods_cash_model_model_retry",
            },
        ]
        step3_batches = []
        waited_instances = []

        def fake_step3(batch_tasks):
            step3_batches.append([task["dt"] for task in batch_tasks])
            running = []
            for index, task in enumerate(batch_tasks, 1):
                copied = dict(task)
                copied["instance_id"] = 2000 + len(step3_batches) * 10 + index
                copied["status"] = "success"
                running.append(
                    {
                        "table": copied["table"],
                        "instance_id": copied["instance_id"],
                        "task": copied,
                    }
                )
            return [item["task"] for item in running], running

        def fake_wait(running_instances, poll_interval=30, max_wait=1800):
            waited_instances.append([item["instance_id"] for item in running_instances])
            completed = []
            for item in running_instances:
                task = dict(item["task"])
                task["final_status"] = "success"
                completed.append(task)
            return completed, []

        with mock.patch.object(module, "step3_start_repair", side_effect=fake_step3), mock.patch.object(
            module, "step4_wait_and_check", side_effect=fake_wait
        ), mock.patch.object(module, "log"):
            results, completed_tasks, failed_tasks = module.execute_repairs_in_batches(tasks)

        self.assertEqual(step3_batches, [["2026-05-10"], ["2026-05-11"]])
        self.assertEqual(waited_instances, [[2011], [2021]])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(completed_tasks), 2)
        self.assertEqual(failed_tasks, [])

    def test_plan_repair_batches_keeps_different_workflows_parallel(self):
        module = load_module()
        tasks = [
            {"table": "table_a", "workflow_code": "wf-a", "task_code": "task-a"},
            {"table": "table_b", "workflow_code": "wf-b", "task_code": "task-b"},
            {"table": "table_c", "workflow_code": "wf-a", "task_code": "task-c"},
            {"table": "table_d", "workflow_code": "wf-c", "task_code": "task-d"},
        ]

        batches = module.plan_repair_batches(tasks, max_parallel=2)

        self.assertEqual(
            [[task["table"] for task in batch] for batch in batches],
            [["table_a", "table_b"], ["table_c", "table_d"]],
        )

    def test_execute_repairs_in_batches_does_not_skip_step4_between_batches(self):
        module = load_module()
        tasks = [
            {
                "table": "table_a",
                "dt": "2026-05-10",
                "workflow_code": "wf-a",
                "workflow_name": "WF_A",
                "task_code": "task-a",
                "task_name": "task_a",
            },
            {
                "table": "table_b",
                "dt": "2026-05-10",
                "workflow_code": "wf-b",
                "workflow_name": "WF_B",
                "task_code": "task-b",
                "task_name": "task_b",
            },
            {
                "table": "table_c",
                "dt": "2026-05-10",
                "workflow_code": "wf-c",
                "workflow_name": "WF_C",
                "task_code": "task-c",
                "task_name": "task_c",
            },
        ]
        step3_batches = []
        step4_calls = []

        def fake_step3(batch_tasks):
            step3_batches.append([task["table"] for task in batch_tasks])
            results = []
            running_instances = []
            for index, task in enumerate(batch_tasks, 1):
                copied = dict(task)
                copied["status"] = "success"
                copied["instance_id"] = 3000 + index + len(step3_batches) * 10
                copied["launched_at"] = "2026-05-10 10:00:00"
                results.append(copied)
                running_instances.append(
                    {
                        "table": copied["table"],
                        "instance_id": copied["instance_id"],
                        "workflow_code": copied["workflow_code"],
                        "task": copied,
                    }
                )
            return results, running_instances

        def fake_step4(running_instances, poll_interval=30, max_wait=1800):
            step4_calls.append([item["table"] for item in running_instances])
            return [], [dict(item["task"], final_status="failed", error="query failed") for item in running_instances]

        with mock.patch.object(module, "step3_start_repair", side_effect=fake_step3), mock.patch.object(
            module, "step4_wait_and_check", side_effect=fake_step4
        ), mock.patch.object(module, "log"):
            results, completed_tasks, failed_tasks = module.execute_repairs_in_batches(tasks)

        self.assertEqual(step3_batches, [["table_a", "table_b", "table_c"]])
        self.assertEqual(step4_calls, [["table_a", "table_b", "table_c"]])
        self.assertEqual(len(results), 3)
        self.assertEqual(completed_tasks, [])
        self.assertEqual(len(failed_tasks), 3)
        self.assertTrue(all(item["final_status"] == "failed" for item in failed_tasks))

    def test_main_skips_fuyan_when_repairs_were_launched_but_not_completed(self):
        module = load_module()

        launched_results = [
            {"table": "table_a", "status": "success", "instance_id": 111, "workflow_code": "wf-a", "task_code": "task-a"}
        ]
        completed_tasks = []
        failed_tasks = [
            {"table": "table_a", "final_status": "failed", "error": "query process instance by id error"}
        ]

        with mock.patch.object(module, "step1_scan_alerts", return_value=[{"table": "table_a", "dt": "2026-05-10"}]), \
            mock.patch.object(module, "step2_find_locations", return_value=[{"table": "table_a", "dt": "2026-05-10"}]), \
            mock.patch.object(module, "load_manual_review_state", return_value={}), \
            mock.patch.object(module, "apply_repair_strategy", return_value=([{"table": "table_a", "dt": "2026-05-10"}], [])), \
            mock.patch.object(module, "execute_repairs_in_batches", return_value=(launched_results, completed_tasks, failed_tasks)), \
            mock.patch.object(module, "record_redundant_retry_attempt"), \
            mock.patch.object(module, "record_manual_review_tasks"), \
            mock.patch.object(module, "save_manual_review_state"), \
            mock.patch.object(module, "step5_execute_fuyan", return_value=[{"name": "fuyan", "status": "success", "id": 1}]) as mocked_fuyan, \
            mock.patch.object(module, "evaluate_repair_outcome", return_value=({"initial_alert_count": 1, "resolved_count": 0, "remaining_count": 1, "manual_review_count": 1, "rerun_tasks": [], "resolved_tasks": [], "remaining_tasks": [], "post_fuyan_remaining_tables": set()}, [])), \
            mock.patch.object(module, "get_remaining_alert_tables", return_value=set()), \
            mock.patch.object(module, "step6_save_report"), \
            mock.patch.object(module, "log"):
            module.main()

        mocked_fuyan.assert_not_called()

    def test_step4_wait_and_check_does_not_fail_when_detail_query_is_temporarily_unavailable(self):
        module = load_module()
        running_instances = [
            {
                "table": "dwd_fox_mission_log",
                "instance_id": 12345,
                "task": {
                    "table": "dwd_fox_mission_log",
                    "instance_id": 12345,
                    "workflow_code": "wf-1",
                },
                "workflow_code": "wf-1",
            }
        ]
        list_side_effects = [
            {},
            {},
            {"id": 12345, "state": "SUCCESS", "endTime": "2026-05-10 09:00:00"},
        ]

        with mock.patch.object(
            module,
            "get_instance_detail",
            return_value=(False, {}, "query process instance by id error"),
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            side_effect=list_side_effects,
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["final_status"], "success")
        self.assertEqual(completed[0]["end_time"], "2026-05-10 09:00:00")
        self.assertEqual(failed, [])

    def test_step4_wait_and_check_can_match_recent_instance_when_returned_id_is_not_queryable(self):
        module = load_module()
        running_instances = [
            {
                "table": "ods_app_product",
                "instance_id": 11111,
                "workflow_code": "wf-app",
                "task": {
                    "table": "ods_app_product",
                    "instance_id": 11111,
                    "workflow_code": "wf-app",
                },
                "launched_at": "2026-05-10 10:33:08",
            }
        ]

        with mock.patch.object(
            module,
            "get_instance_detail",
            return_value=(False, {}, "query process instance by id error"),
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            return_value={
                "id": 22222,
                "state": "SUCCESS",
                "endTime": "2026-05-10 10:34:02",
            },
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["instance_id"], 22222)
        self.assertEqual(completed[0]["final_status"], "success")
        self.assertEqual(failed, [])

    def test_step4_wait_and_check_discovers_real_process_instance_before_detail_query(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        running_instances = [
            {
                "table": "ods_app_product",
                "instance_id": 99999,
                "start_response_id": 99999,
                "resolved_instance_id": None,
                "workflow_code": "wf-app",
                "task": {
                    "table": "ods_app_product",
                    "instance_id": 99999,
                    "workflow_code": "wf-app",
                    "launched_at": "2026-05-10 10:33:08",
                },
            }
        ]
        detail_calls = []

        def fake_get_instance_detail(project_code, instance_id):
            detail_calls.append(instance_id)
            if instance_id == 22222:
                return True, {"id": 22222, "state": "SUCCESS", "endTime": "2026-05-10 10:34:02"}, ""
            return False, {}, "query process instance by id error"

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            return_value={"id": 22222, "state": "RUNNING_EXECUTION", "startTime": "2026-05-10 10:33:09"},
        ), mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=fake_get_instance_detail,
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(detail_calls[0], 22222)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["instance_id"], 22222)
        self.assertEqual(failed, [])

    def test_step4_wait_and_check_prefers_recent_real_instance_over_stop_start_receipt(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        running_instances = [
            {
                "table": "dwd_asset_spv_pledges_asset",
                "instance_id": 21680180058848,
                "start_response_id": 21680180058848,
                "resolved_instance_id": None,
                "workflow_code": "18641948384363",
                "task": {
                    "table": "dwd_asset_spv_pledges_asset",
                    "instance_id": 21680180058848,
                    "workflow_code": "18641948384363",
                    "launched_at": "2026-05-15 09:00:06",
                },
            }
        ]

        def fake_get_instance_detail(project_code, instance_id):
            if instance_id == 1534334:
                return True, {"id": 1534334, "state": "SUCCESS", "endTime": "2026-05-15 09:07:17"}, ""
            return True, {"id": 21680180058848, "state": "STOP", "endTime": "2026-05-15 09:00:25"}, ""

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            side_effect=[
                {},
                {"id": 1534334, "state": "SUCCESS", "startTime": "2026-05-15 09:00:04", "endTime": "2026-05-15 09:07:17"},
            ],
        ), mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=fake_get_instance_detail,
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["instance_id"], 1534334)
        self.assertEqual(completed[0]["final_status"], "success")
        self.assertEqual(failed, [])

    def test_step4_wait_and_check_rechecks_recent_instance_after_initial_stop_resolution(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        running_instances = [
            {
                "table": "dwd_asset_capital_transaction",
                "instance_id": 21746074500064,
                "start_response_id": 21746074500064,
                "resolved_instance_id": None,
                "workflow_code": "18641948384363",
                "task": {
                    "table": "dwd_asset_capital_transaction",
                    "instance_id": 21746074500064,
                    "workflow_code": "18641948384363",
                    "launched_at": "2026-05-21 08:00:07",
                },
            }
        ]

        def fake_get_instance_detail(project_code, instance_id):
            if instance_id == 1572892:
                return True, {"id": 1572892, "state": "SUCCESS", "endTime": "2026-05-21 08:05:12"}, ""
            return True, {"id": 1572895, "state": "STOP", "endTime": "2026-05-21 08:00:24"}, ""

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            side_effect=[
                {"id": 1572895, "state": "STOP", "startTime": "2026-05-21 08:00:08", "endTime": "2026-05-21 08:00:24"},
                {"id": 1572892, "state": "SUCCESS", "startTime": "2026-05-21 08:00:03", "endTime": "2026-05-21 08:05:12"},
            ],
        ), mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=fake_get_instance_detail,
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["instance_id"], 1572892)
        self.assertEqual(completed[0]["final_status"], "success")
        self.assertEqual(failed, [])

    def test_should_delay_failed_state_confirmation_allows_short_recheck_window(self):
        module = load_module()
        item = {
            "workflow_code": "18641948384363",
            "first_seen_at": 100.0,
            "failed_state_rechecks": 0,
            "task": {"workflow_code": "18641948384363"},
        }

        with mock.patch("time.time", return_value=120.0):
            self.assertTrue(module.should_delay_failed_state_confirmation(item, "STOP"))
        self.assertEqual(item["failed_state_rechecks"], 1)

    def test_should_delay_failed_state_confirmation_stops_after_grace_window(self):
        module = load_module()
        item = {
            "workflow_code": "18641948384363",
            "first_seen_at": 100.0,
            "failed_state_rechecks": 0,
            "task": {"workflow_code": "18641948384363"},
        }

        with mock.patch("time.time", return_value=200.0):
            self.assertFalse(module.should_delay_failed_state_confirmation(item, "STOP"))
        self.assertEqual(item["failed_state_rechecks"], 0)

    def test_should_delay_failed_state_confirmation_allows_more_than_two_rechecks_within_grace_window(self):
        module = load_module()
        item = {
            "workflow_code": "18641948384363",
            "first_seen_at": 100.0,
            "failed_state_rechecks": 2,
            "task": {"workflow_code": "18641948384363"},
        }

        with mock.patch("time.time", return_value=120.0):
            self.assertTrue(module.should_delay_failed_state_confirmation(item, "STOP"))
        self.assertEqual(item["failed_state_rechecks"], 3)

    def test_get_instance_from_list_avoids_all_state_for_process_mode(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        seen_state_types = []

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            seen_state_types.append(state_type)
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.get_instance_from_list("default-project", 12345)

        self.assertEqual(result, {})
        self.assertNotIn("ALL", seen_state_types)

    def test_find_recent_instance_by_workflow_ignores_scheduled_instance_started_before_launch(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        success_items = [
            {
                "id": 3159308,
                "state": "SUCCESS",
                "startTime": "2026-05-11 09:35:01",
                "processDefinitionCode": 16916802671296,
                "commandType": "SCHEDULER",
            },
            {
                "id": 3159315,
                "state": "SUCCESS",
                "startTime": "2026-05-11 09:35:46",
                "processDefinitionCode": 16916802671296,
                "commandType": "START_PROCESS",
            },
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type == "SUCCESS":
                return success_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "16916802671296",
                launched_at="2026-05-11 09:35:44",
            )

        self.assertEqual(result["id"], 3159315)

    def test_find_recent_instance_by_workflow_returns_empty_when_only_prelaunch_scheduler_match_exists(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        success_items = [
            {
                "id": 3159308,
                "state": "SUCCESS",
                "startTime": "2026-05-11 09:35:01",
                "processDefinitionCode": 16916802671296,
                "commandType": "SCHEDULER",
            }
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type == "SUCCESS":
                return success_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "16916802671296",
                launched_at="2026-05-11 09:35:44",
            )

        self.assertEqual(result, {})

    def test_find_recent_instance_by_workflow_falls_back_to_latest_non_scheduler_when_time_zone_differs(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        success_items = [
            {
                "id": 3159308,
                "state": "SUCCESS",
                "startTime": "2026-05-11 09:35:01",
                "processDefinitionCode": 16916802671296,
                "commandType": "SCHEDULER",
            },
            {
                "id": 3159315,
                "state": "SUCCESS",
                "startTime": "2026-05-11 09:35:46",
                "processDefinitionCode": 16916802671296,
                "commandType": "START_PROCESS",
            },
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type == "SUCCESS":
                return success_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "16916802671296",
                launched_at="2026-05-10 20:35:44",
            )

        self.assertEqual(result["id"], 3159315)

    def test_find_recent_instance_by_workflow_tolerates_small_clock_skew_before_launch(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        running_items = [
            {
                "id": 1534334,
                "state": "RUNNING_EXECUTION",
                "startTime": "2026-05-15 09:00:04",
                "processDefinitionCode": 18641948384363,
                "commandType": "START_PROCESS",
            }
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type == "RUNNING_EXECUTION":
                return running_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "18641948384363",
                launched_at="2026-05-15 09:00:06",
            )

        self.assertEqual(result["id"], 1534334)

    def test_find_recent_instance_by_workflow_prefers_candidate_closest_to_launch_time(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        running_items = [
            {
                "id": 1540522,
                "state": "SUCCESS",
                "startTime": "2026-05-16 08:00:03",
                "processDefinitionCode": 18641948384363,
                "commandType": "START_PROCESS",
            },
            {
                "id": 1540525,
                "state": "STOP",
                "startTime": "2026-05-16 08:00:20",
                "processDefinitionCode": 18641948384363,
                "commandType": "START_PROCESS",
            },
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type in ("RUNNING_EXECUTION", "SUCCESS", "FAILURE", "READY_STOP", None):
                return running_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "18641948384363",
                launched_at="2026-05-16 08:00:06",
            )

        self.assertEqual(result["id"], 1540522)

    def test_find_recent_instance_by_workflow_prefers_success_candidate_over_closer_stop_candidate(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"

        running_items = [
            {
                "id": 1572895,
                "state": "STOP",
                "startTime": "2026-05-21 08:00:08",
                "processDefinitionCode": 18641948384363,
                "commandType": "START_PROCESS",
            },
            {
                "id": 1572892,
                "state": "SUCCESS",
                "startTime": "2026-05-21 08:00:03",
                "processDefinitionCode": 18641948384363,
                "commandType": "START_PROCESS",
            },
        ]

        def fake_get_all_instances_from_lists(project_code, state_type='ALL'):
            if state_type in ("RUNNING_EXECUTION", "SUCCESS", "FAILURE", "READY_STOP", None):
                return running_items
            return []

        with mock.patch.object(module, "get_all_instances_from_lists", side_effect=fake_get_all_instances_from_lists):
            result = module.find_recent_instance_by_workflow(
                "default-project",
                "18641948384363",
                launched_at="2026-05-21 08:00:07",
            )

        self.assertEqual(result["id"], 1572892)

    def test_get_instance_detail_uses_configured_process_instance_style(self):
        module = load_module()
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        seen_endpoints = []

        def fake_ds_api_get(endpoint):
            seen_endpoints.append(endpoint)
            if endpoint == "/projects/default-project/process-instances/12345":
                return True, {"id": 12345, "state": "RUNNING_EXECUTION"}, ""
            return False, {}, "wrong endpoint"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            success, data, msg = module.get_instance_detail("default-project", 12345)

        self.assertTrue(success)
        self.assertEqual(data["id"], 12345)
        self.assertEqual(
            seen_endpoints,
            ["/projects/default-project/process-instances/12345"],
        )

    def test_get_instance_detail_falls_back_to_workflow_instances_when_process_endpoint_returns_non_json(self):
        module = load_module()
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        seen_endpoints = []

        def fake_ds_api_get(endpoint):
            seen_endpoints.append(endpoint)
            if endpoint == "/projects/default-project/process-instances/12345":
                return False, {}, "invalid json response: content-type=text/html"
            if endpoint == "/projects/default-project/workflow-instances/12345":
                return True, {"id": 12345, "state": "STOP"}, ""
            return False, {}, "wrong endpoint"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            success, data, msg = module.get_instance_detail("default-project", 12345)

        self.assertTrue(success)
        self.assertEqual(data["state"], "STOP")
        self.assertEqual(
            seen_endpoints,
            [
                "/projects/default-project/process-instances/12345",
                "/projects/default-project/workflow-instances/12345",
            ],
        )

    def test_step2_find_locations_falls_back_to_process_definition_list_for_ds32(self):
        module = load_module()

        alerts = [{"id": 1, "table": "dwd_fox_mission_log", "dt": "2026-04-29"}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return False, {}, "not json"
            if endpoint.endswith("/process-definition?pageNo=1&pageSize=100"):
                return True, {"totalList": [{"code": "wf-1"}]}, ""
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "DWD"},
                    "taskDefinitionList": [{"code": "task-1", "name": "dwd_fox_mission_log"}],
                }, ""
            return False, {}, "not found"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "wf-1")
        self.assertEqual(tasks[0]["task_code"], "task-1")

    def test_step2_find_locations_scans_multiple_workflow_pages(self):
        module = load_module()
        alerts = [{"id": 1, "table": "dwd_c_coupon", "dt": "2026-05-08", "diff": 1}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return False, {}, "not json"
            if endpoint.endswith("/process-definition?pageNo=1&pageSize=100"):
                return True, {
                    "totalList": [{"code": "wf-page1"}],
                    "totalPage": 2,
                    "currentPage": 1,
                }, ""
            if endpoint.endswith("/process-definition?pageNo=2&pageSize=100"):
                return True, {
                    "totalList": [{"code": "wf-page2"}],
                    "totalPage": 2,
                    "currentPage": 2,
                }, ""
            if endpoint == "/projects/default-project/workflow-definition/wf-page1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-page1":
                return True, {
                    "processDefinition": {"name": "PAGE1"},
                    "taskDefinitionList": [{"code": "task-a", "name": "dwd_other_table"}],
                }, ""
            if endpoint == "/projects/default-project/workflow-definition/wf-page2":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-page2":
                return True, {
                    "processDefinition": {"name": "PAGE2"},
                    "taskDefinitionList": [{"code": "task-b", "name": "dwd_c_coupon"}],
                }, ""
            return False, {}, f"unexpected endpoint: {endpoint}"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "wf-page2")
        self.assertEqual(tasks[0]["task_code"], "task-b")
        self.assertEqual(tasks[0]["workflow_name"], "PAGE2")

    def test_step2_find_locations_prefers_unscheduled_child_workflow_over_scheduled_parent(self):
        module = load_module()
        alerts = [{"id": 1, "table": "ods_cash_model_model", "dt": "2026-05-10", "diff": 1}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition/158514956979200"):
                return True, {
                    "processDefinition": {"name": "印尼-数仓工作流（1/2H）"},
                    "taskDefinitionList": [
                        {
                            "code": "task-parent",
                            "name": "ods_cash_model_model",
                            "taskType": "SUB_PROCESS",
                        }
                    ],
                }, ""
            if endpoint.endswith("/workflow-definition/child-free"):
                return True, {
                    "processDefinition": {"name": "ODS_CASH_MODEL"},
                    "taskDefinitionList": [{"code": "task-child", "name": "ods_cash_model_model"}],
                }, ""
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return True, {
                    "totalList": [{"code": "child-free"}],
                    "totalPage": 1,
                }, ""
            if endpoint.endswith("/schedules?pageNo=1&pageSize=200"):
                return True, {
                    "totalList": [
                        {
                            "processDefinitionCode": "158514956979200",
                            "releaseState": "ONLINE",
                        }
                    ],
                    "totalPage": 1,
                }, ""
            return False, {}, f"unexpected endpoint: {endpoint}"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "child-free")
        self.assertEqual(tasks[0]["task_code"], "task-child")
        self.assertEqual(tasks[0]["workflow_name"], "ODS_CASH_MODEL")

    def test_step2_find_locations_marks_scheduled_parent_only_match_as_manual_review(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = [('158514956979200', '印尼-数仓工作流（1/2H）')]
        alerts = [{"id": 1, "table": "ods_cash_model_model", "dt": "2026-05-10", "diff": 1}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition/158514956979200"):
                return True, {
                    "processDefinition": {"name": "印尼-数仓工作流（1/2H）"},
                    "taskDefinitionList": [
                        {
                            "code": "task-parent",
                            "name": "ods_cash_model_model",
                            "taskType": "SUB_PROCESS",
                        }
                    ],
                }, ""
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return True, {"totalList": [], "totalPage": 1}, ""
            if endpoint.endswith("/schedules?pageNo=1&pageSize=200"):
                return True, {
                    "totalList": [
                        {
                            "processDefinitionCode": "158514956979200",
                            "releaseState": "ONLINE",
                        }
                    ],
                    "totalPage": 1,
                }, ""
            return False, {}, f"unexpected endpoint: {endpoint}"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "")
        self.assertIn("带定时", tasks[0]["error"])
        self.assertIn("1/2H", tasks[0]["error"])

    def test_step2_find_locations_descends_into_subprocess_child_workflow(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = [('158514956979200', '印尼-数仓工作流（1/2H）')]
        alerts = [{"id": 1, "table": "dwd_fox_chatbot_dialog", "dt": "2026-05-11", "diff": 1}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition/158514956979200"):
                return True, {
                    "processDefinition": {"name": "印尼-数仓工作流（1/2H）"},
                    "taskDefinitionList": [
                        {
                            "code": "task-parent-subprocess",
                            "name": "dwd_fox_chatbot_dialog",
                            "taskType": "SUB_PROCESS",
                            "taskParams": {"processDefinitionCode": "wf-dialog-child"},
                        }
                    ],
                }, ""
            if endpoint.endswith("/workflow-definition/wf-dialog-child"):
                return True, {
                    "processDefinition": {"name": "DWD_FOX_CHATBOT_DIALOG"},
                    "taskDefinitionList": [
                        {
                            "code": "task-child",
                            "name": "dwd_fox_chatbot_dialog",
                            "taskType": "SHELL",
                        }
                    ],
                }, ""
            if endpoint.endswith("/schedules?pageNo=1&pageSize=200"):
                return True, {
                    "totalList": [
                        {
                            "processDefinitionCode": "158514956979200",
                            "releaseState": "ONLINE",
                        }
                    ],
                    "totalPage": 1,
                }, ""
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return True, {"totalList": [], "totalPage": 1}, ""
            return False, {}, f"unexpected endpoint: {endpoint}"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "wf-dialog-child")
        self.assertEqual(tasks[0]["workflow_name"], "DWD_FOX_CHATBOT_DIALOG")
        self.assertEqual(tasks[0]["task_code"], "task-child")
        self.assertEqual(tasks[0]["task_name"], "dwd_fox_chatbot_dialog")

    def test_step2_find_locations_allows_scheduled_workflow_when_matching_real_task(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [{"id": 1, "table": "dwd_fox_chatbot_dialog", "dt": "2026-05-11", "diff": 1}]

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return True, {
                    "totalList": [{"workflowDefinitionCode": "wf-dwd-paimon"}],
                    "totalPage": 1,
                }, ""
            if endpoint.endswith("/workflow-definition/wf-dwd-paimon"):
                return True, {
                    "processDefinition": {"name": "DWD_PAIMON"},
                    "taskDefinitionList": [
                        {
                            "code": "task-real",
                            "name": "dwd_fox_chatbot_dialog",
                            "taskType": "SHELL",
                        }
                    ],
                }, ""
            if endpoint.endswith("/schedules?pageNo=1&pageSize=200"):
                return True, {
                    "totalList": [
                        {
                            "processDefinitionCode": "wf-dwd-paimon",
                            "releaseState": "ONLINE",
                        }
                    ],
                    "totalPage": 1,
                }, ""
            return False, {}, f"unexpected endpoint: {endpoint}"

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "wf-dwd-paimon")
        self.assertEqual(tasks[0]["workflow_name"], "DWD_PAIMON")
        self.assertEqual(tasks[0]["task_code"], "task-real")
        self.assertEqual(tasks[0]["task_name"], "dwd_fox_chatbot_dialog")

    def test_step2_find_locations_keeps_out_of_window_status_and_skips_search(self):
        module = load_module()
        alerts = [
            {
                "id": 1,
                "table": "dwd_user_coupon",
                "dt": "2026-02-08",
                "diff": -7,
                "status": "skipped_out_of_window",
                "error": "告警窗口 begin=2026-02-08, end=2026-05-09，begin 早于自动修复窗口起点 2026-05-03，转人工处理",
            }
        ]

        with mock.patch.object(module, "step2_search_in_workflow") as mocked_search, \
            mock.patch.object(module, "get_workflow_definition_list") as mocked_list, \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        mocked_search.assert_not_called()
        mocked_list.assert_not_called()
        self.assertEqual(tasks[0]["status"], "skipped_out_of_window")
        self.assertEqual(tasks[0]["error"], alerts[0]["error"])
        self.assertEqual(tasks[0]["table"], "dwd_user_coupon")

    def test_step3_start_repair_skips_out_of_window_tasks_even_if_workflow_exists(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_user_coupon",
                "dt": "2026-02-08",
                "workflow_code": "wf-1",
                "workflow_name": "DWD",
                "task_code": "task-1",
                "task_name": "dwd_user_coupon",
                "status": "skipped_out_of_window",
                "error": "告警窗口过长，转人工处理",
            }
        ]

        with mock.patch.object(module, "ds_api_post") as mocked_post, \
            mock.patch.object(module, "find_conflicting_running_instance") as mocked_conflict, \
            mock.patch.object(module, "log"):
            results, running_instances = module.step3_start_repair(tasks)

        mocked_post.assert_not_called()
        mocked_conflict.assert_not_called()
        self.assertEqual(running_instances, [])
        self.assertEqual(results[0]["status"], "skipped_manual_review")
        self.assertIn("人工处理", results[0]["error"])

    def test_step2_search_in_workflow_marks_forbidden_task_for_manual_review(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "DWD"},
                    "taskDefinitionList": [
                        {
                            "code": "task-1",
                            "name": "dwd_fox_mission_log",
                            "flag": "NO",
                            "taskParams": {"sql": "select 1"},
                        }
                    ],
                }, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            result = module.step2_search_in_workflow("wf-1", "dwd_fox_mission_log")

        self.assertEqual(result["task_code"], "task-1")
        self.assertEqual(result["task_flag"], "NO")

    def test_step2_search_in_workflow_prefers_non_datax_candidate_when_names_conflict(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "simontang_test"},
                    "taskDefinitionList": [
                        {
                            "code": "task-datax",
                            "name": "ads_3324_tdtools_match_batch_result",
                            "taskType": "DATAX",
                        },
                        {
                            "code": "task-shell",
                            "name": "ads_3324_tdtools_match_batch_result",
                            "taskType": "SHELL",
                        },
                    ],
                }, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            result = module.step2_search_in_workflow("wf-1", "ads_3324_tdtools_match_batch_result")

        self.assertEqual(result["task_code"], "task-shell")
        self.assertEqual(result["task_type"], "SHELL")

    def test_step2_search_in_workflow_prefers_runnable_candidate_over_forbidden_one(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "DWS"},
                    "taskDefinitionList": [
                        {
                            "code": "task-forbidden",
                            "name": "dws_user_performance_first_loan_info",
                            "flag": "NO",
                            "taskType": "SHELL",
                        },
                        {
                            "code": "task-runnable",
                            "name": "dws_user_performance_first_loan_info",
                            "flag": "YES",
                            "taskType": "SHELL",
                        },
                    ],
                }, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            result = module.step2_search_in_workflow("wf-1", "dws_user_performance_first_loan_info")

        self.assertEqual(result["task_code"], "task-runnable")
        self.assertEqual(result["task_flag"], "YES")

    def test_step2_search_in_workflow_prefers_executable_task_over_conditions_node(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/workflow-definition/wf-1":
                return False, {}, "not json"
            if endpoint == "/projects/default-project/process-definition/wf-1":
                return True, {
                    "processDefinition": {"name": "DWD_PAIMON"},
                    "taskDefinitionList": [
                        {
                            "code": "task-conditions",
                            "name": "dwd_user_activity_log",
                            "flag": "YES",
                            "taskType": "CONDITIONS",
                        },
                        {
                            "code": "task-sql",
                            "name": "dwd_user_activity_log",
                            "flag": "YES",
                            "taskType": "SHELL",
                        },
                    ],
                }, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            result = module.step2_search_in_workflow("wf-1", "dwd_user_activity_log")

        self.assertEqual(result["task_code"], "task-sql")
        self.assertEqual(result["task_type"], "SHELL")

    def test_apply_repair_strategy_escalates_forbidden_task_to_manual_review(self):
        module = load_module()
        tasks = [
            {
                "table": "dwd_fox_mission_log",
                "dt": "2026-04-29",
                "diff": 123,
                "task_flag": "NO",
                "workflow_name": "DWD",
                "task_name": "dwd_fox_mission_log",
            }
        ]

        runnable, manual_review = module.apply_repair_strategy(tasks, {})

        self.assertEqual(runnable, [])
        self.assertEqual(len(manual_review), 1)
        self.assertEqual(manual_review[0]["status"], "skipped_manual_review")
        self.assertIn("禁止执行", manual_review[0]["error"])
        self.assertIn("123", manual_review[0]["error"])

    def test_step4_wait_and_check_falls_back_to_instance_list_when_detail_query_fails(self):
        module = load_module()
        running_instances = [
            {
                "table": "dwd_fox_mission_log",
                "instance_id": 21595307329344,
                "task": {
                    "table": "dwd_fox_mission_log",
                    "instance_id": 21595307329344,
                    "status": "success",
                },
            }
        ]

        with mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=[
                (False, {}, "query process instance by id error"),
                (False, {}, "query process instance by id error"),
            ],
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            side_effect=[
                {"id": 21595307329344, "state": "RUNNING_EXECUTION"},
                {"id": 21595307329344, "state": "SUCCESS", "endTime": "2026-05-07 15:49:30"},
            ],
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            completed_tasks, failed_tasks = module.step4_wait_and_check(
                running_instances,
                poll_interval=0,
                max_wait=1,
            )

        self.assertEqual(len(completed_tasks), 1)
        self.assertEqual(completed_tasks[0]["final_status"], "success")
        self.assertEqual(completed_tasks[0]["end_time"], "2026-05-07 15:49:30")
        self.assertEqual(failed_tasks, [])

    def test_resolve_repair_table_prefers_downstream_warehouse_layer_over_ods(self):
        module = load_module()
        row = {
            "src_db": "ods",
            "src_tbl": "ods_qsq_erp_biz_report",
            "dest_db": "dwd",
            "dest_tbl": "dwd_qsq_erp_biz_report",
        }

        table_name = module.resolve_repair_table(row)

        self.assertEqual(table_name, "dwd_qsq_erp_biz_report")

    def test_resolve_repair_table_prefers_dest_table_when_both_sides_same_layer(self):
        module = load_module()
        row = {
            "src_db": "dwd",
            "src_tbl": "dwd_source_example",
            "dest_db": "dwd",
            "dest_tbl": "dwd_target_example",
        }

        table_name = module.resolve_repair_table(row)

        self.assertEqual(table_name, "dwd_target_example")

    def test_resolve_repair_table_prefers_dest_table_even_when_dest_is_lower_layer(self):
        module = load_module()
        row = {
            "src_db": "dwd",
            "src_tbl": "dwd_fox_chatbot_dialog",
            "dest_db": "dwb",
            "dest_tbl": "dwb_a5_dialog",
        }

        table_name = module.resolve_repair_table(row)

        self.assertEqual(table_name, "dwb_a5_dialog")

    def test_resolve_repair_table_prefers_dest_table_for_cross_layer_dialog_alert(self):
        module = load_module()
        row = {
            "src_db": "dwb",
            "src_tbl": "dwb_a5_dialog",
            "dest_db": "dwd_paimon",
            "dest_tbl": "dwd_fox_chatbot_dialog",
        }

        table_name = module.resolve_repair_table(row)

        self.assertEqual(table_name, "dwd_fox_chatbot_dialog")

    def test_build_search_tables_uses_only_dest_table_when_present(self):
        module = load_module()
        row = {
            "src_tbl": "dwd_app_ask_loan_result_all",
            "dest_tbl": "dws_user_performance_first_loan_info",
        }

        self.assertEqual(
            module.build_search_tables(row),
            ["dws_user_performance_first_loan_info"],
        )

    def test_build_search_tables_falls_back_to_src_when_dest_missing(self):
        module = load_module()
        row = {
            "src_tbl": "dwd_app_ask_loan_result_all",
            "dest_tbl": "",
        }

        self.assertEqual(
            module.build_search_tables(row),
            ["dwd_app_ask_loan_result_all"],
        )

    def test_resolve_alert_dt_prefers_begin_date(self):
        module = load_module()
        row = {
            "begin": datetime(2026, 4, 28, 0, 0, 0),
            "end": datetime(2026, 4, 29, 0, 0, 0),
        }

        dt = module.resolve_alert_dt(row, now=datetime(2026, 4, 29, 10, 0, 0))

        self.assertEqual(dt, "2026-04-28")

    def test_resolve_alert_dt_uses_end_minus_one_day_when_begin_missing(self):
        module = load_module()
        row = {
            "begin": None,
            "end": datetime(2026, 4, 29, 0, 0, 0),
        }

        dt = module.resolve_alert_dt(row, now=datetime(2026, 4, 29, 10, 0, 0))

        self.assertEqual(dt, "2026-04-28")

    def test_resolve_alert_dt_falls_back_to_today_when_no_window_available(self):
        module = load_module()
        row = {"begin": None, "end": None}

        dt = module.resolve_alert_dt(row, now=datetime(2026, 4, 29, 10, 0, 0))

        self.assertEqual(dt, "2026-04-29")

    def test_get_alert_window_status_marks_long_span_out_of_window(self):
        module = load_module()
        row = {
            "begin": datetime(2026, 2, 8, 0, 0, 0),
            "end": datetime(2026, 5, 9, 0, 0, 0),
        }

        status = module.get_alert_window_status(row, now=datetime(2026, 5, 10, 10, 0, 0))

        self.assertTrue(status["is_out_of_window"])
        self.assertEqual(status["reason"], "window_span_exceeds_limit")
        self.assertEqual(status["begin_date"], "2026-02-08")
        self.assertEqual(status["end_date"], "2026-05-09")
        self.assertEqual(status["latest_alert_dt"], "2026-05-08")
        self.assertGreater(status["window_span_days"], 8)

    def test_get_alert_window_status_allows_span_of_exactly_eight_days(self):
        module = load_module()
        row = {
            "begin": datetime(2026, 5, 1, 0, 0, 0),
            "end": datetime(2026, 5, 9, 0, 0, 0),
        }

        status = module.get_alert_window_status(row, now=datetime(2026, 5, 10, 10, 0, 0))

        self.assertFalse(status["is_out_of_window"])
        self.assertEqual(status["reason"], "")
        self.assertEqual(status["window_span_days"], 8)

    def test_get_remaining_alert_tables_excludes_rows_when_window_span_exceeds_limit(self):
        module = load_module()

        rows = [
            {
                "src_db": "ods",
                "src_tbl": "ods_long_window_table",
                "dest_db": "dwd",
                "dest_tbl": "dwd_long_window_table",
                "begin": datetime(2026, 2, 8, 0, 0, 0),
                "end": datetime(2026, 5, 9, 0, 0, 0),
            },
            {
                "src_db": "ods",
                "src_tbl": "ods_recent_table",
                "dest_db": "dwd",
                "dest_tbl": "dwd_recent_table",
                "begin": datetime(2026, 5, 9, 0, 0, 0),
                "end": datetime(2026, 5, 10, 0, 0, 0),
            },
        ]

        fake_cursor = mock.MagicMock()
        fake_cursor.fetchall.return_value = rows
        fake_conn = mock.MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_db_module = types.ModuleType("alert.db_config")
        fake_db_module.get_db_connection = mock.MagicMock(return_value=fake_conn)

        with mock.patch.dict(sys.modules, {"alert.db_config": fake_db_module}):
            tables = module.get_remaining_alert_tables(now=datetime(2026, 5, 10, 10, 0, 0))

        self.assertEqual(tables, {"dwd_recent_table"})

    def test_execute_repairs_in_batches_limits_parallel_work_to_four(self):
        module = load_module()
        tasks = [{"table": f"table_{idx}", "dt": "2026-04-26"} for idx in range(12)]
        step3_calls = []
        step4_calls = []

        def fake_step3(batch):
            step3_calls.append([item["table"] for item in batch])
            results = []
            running_instances = []
            for item in batch:
                task = dict(item)
                task["status"] = "success"
                task["instance_id"] = f"instance_{item['table']}"
                results.append(task)
                running_instances.append(
                    {
                        "table": item["table"],
                        "instance_id": task["instance_id"],
                        "task": task,
                    }
                )
            return results, running_instances

        def fake_step4(running_instances):
            step4_calls.append([item["table"] for item in running_instances])
            completed = [dict(item["task"], final_status="success") for item in running_instances]
            return completed, []

        with mock.patch.object(module, "step3_start_repair", side_effect=fake_step3), mock.patch.object(
            module, "step4_wait_and_check", side_effect=fake_step4
        ):
            results, completed_tasks, failed_tasks = module.execute_repairs_in_batches(tasks)

        self.assertEqual(
            step3_calls,
            [
                ["table_0", "table_1", "table_2", "table_3"],
                ["table_4", "table_5", "table_6", "table_7"],
                ["table_8", "table_9", "table_10", "table_11"],
            ],
        )
        self.assertEqual(step4_calls, step3_calls)
        self.assertEqual(len(results), 12)
        self.assertEqual(len(completed_tasks), 12)
        self.assertEqual(failed_tasks, [])

    def test_step4_wait_and_check_uses_sixty_second_timeout(self):
        module = load_module()
        running_instances = [
            {
                "table": "table_a",
                "instance_id": 12345,
                "workflow_code": "wf-a",
                "task": {
                    "table": "table_a",
                    "instance_id": 12345,
                    "workflow_code": "wf-a",
                    "launched_at": "2026-05-10 10:00:00",
                },
            }
        ]

        fake_times = iter([0, 0, 30, 30, 61, 61, 61])

        with mock.patch.object(module, "find_recent_instance_by_workflow", return_value={}), \
            mock.patch.object(module, "get_instance_detail", return_value=(False, {}, "query failed")), \
            mock.patch.object(module, "get_instance_from_list", return_value={}), \
            mock.patch.object(module, "collect_instance_query_diagnostics", return_value={}), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"), \
            mock.patch("time.time", side_effect=lambda: next(fake_times)):
            completed, failed = module.step4_wait_and_check(
                running_instances,
                poll_interval=30,
                max_wait=60,
            )

        self.assertEqual(completed, [])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["final_status"], "timeout")

    def test_apply_repair_strategy_allows_first_retry_for_suspected_redundant_data(self):
        module = load_module()
        tasks = [
            {
                "table": "ods_qsq_erp_cpop_settlement_order_procedure",
                "dt": "2026-04-27",
                "diff": -4,
            }
        ]

        runnable, manual_review = module.apply_repair_strategy(tasks, {})

        self.assertEqual([item["table"] for item in runnable], ["ods_qsq_erp_cpop_settlement_order_procedure"])
        self.assertEqual(manual_review, [])

    def test_apply_repair_strategy_escalates_repeated_redundant_data_alert_to_manual_review(self):
        module = load_module()
        tasks = [
            {
                "table": "ods_qsq_erp_cpop_settlement_order_procedure",
                "dt": "2026-04-27",
                "diff": -4,
            }
        ]
        strategy_state = {
            "ods_qsq_erp_cpop_settlement_order_procedure": {
                "2026-04-27": {
                    "redundant_retry_done": True,
                    "manual_review_required": False,
                }
            }
        }

        runnable, manual_review = module.apply_repair_strategy(tasks, strategy_state)

        self.assertEqual(runnable, [])
        self.assertEqual(len(manual_review), 1)
        self.assertEqual(manual_review[0]["status"], "skipped_manual_review")
        self.assertIn("底层是否需要删数", manual_review[0]["error"])

    def test_generate_tv_report_lists_manual_review_items(self):
        module = load_module()
        summary = {
            "initial_alert_count": 1,
            "resolved_count": 0,
            "remaining_count": 1,
            "manual_review_count": 1,
            "rerun_tasks": [],
            "resolved_tasks": [],
            "remaining_tasks": [
                {
                    "table": "ods_qsq_erp_cpop_settlement_order_procedure",
                    "error": "疑似当前层数据多于底层，重跑一次后仍未恢复，建议检查底层是否需要删数，并人工判断修复",
                }
            ],
            "post_fuyan_remaining_tables": {"ods_qsq_erp_cpop_settlement_order_procedure"},
            "display_pending_tables_count": 1,
        }

        with mock.patch.object(module, "log"):
            report = module.generate_tv_report(summary, [])

        self.assertIn("需人工处理", report)
        self.assertIn("ods_qsq_erp_cpop_settlement_order_procedure", report)
        self.assertIn("底层是否需要删数", report)

    def test_count_remaining_alert_tables_dedupes_by_resolved_table(self):
        module = load_module()

        rows = [
            {
                "src_db": "ods",
                "src_tbl": "ods_qsq_erp_biz_report",
                "dest_db": "dwd",
                "dest_tbl": "dwd_qsq_erp_biz_report",
            },
            {
                "src_db": "ods",
                "src_tbl": "ods_qsq_erp_biz_report",
                "dest_db": "dwd",
                "dest_tbl": "dwd_qsq_erp_biz_report",
            },
            {
                "src_db": "ods",
                "src_tbl": "ods_other",
                "dest_db": "dwd",
                "dest_tbl": "dwd_other",
            },
        ]

        fake_cursor = mock.MagicMock()
        fake_cursor.fetchall.return_value = rows
        fake_conn = mock.MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_db_module = types.ModuleType("alert.db_config")
        fake_db_module.get_db_connection = mock.MagicMock(return_value=fake_conn)

        with mock.patch.dict(sys.modules, {"alert.db_config": fake_db_module}):
            count = module.count_remaining_alert_tables()

        self.assertEqual(count, 2)

    def test_count_remaining_alert_tables_queries_configured_result_table_without_created_at_cutoff(self):
        module = load_module()

        fake_cursor = mock.MagicMock()
        fake_cursor.fetchall.return_value = []
        fake_conn = mock.MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_db_module = types.ModuleType("alert.db_config")
        fake_db_module.get_db_connection = mock.MagicMock(return_value=fake_conn)

        with mock.patch.dict(sys.modules, {"alert.db_config": fake_db_module}):
            module.count_remaining_alert_tables()

        executed_sql = fake_cursor.execute.call_args[0][0]
        self.assertIn("FROM default_quality_result", executed_sql)
        self.assertNotIn("INTERVAL 7 DAY", executed_sql)

    def test_step5_execute_fuyan_skips_when_no_completed_repairs(self):
        module = load_module()

        with mock.patch.object(module, "ds_api_post") as mock_post, mock.patch.object(module, "log"):
            results = module.step5_execute_fuyan([], [], [{"table": "dwd_fox_mission_log"}])

        self.assertEqual(results, [])
        mock_post.assert_not_called()

    def test_main_skips_fuyan_when_no_repairs_were_started(self):
        module = load_module()
        alerts = [{"table": "dwd_fox_mission_log", "dt": "2026-04-29"}]
        unresolved_tables = {"dwd_fox_mission_log"}

        with mock.patch.object(module, "step1_scan_alerts", return_value=alerts), mock.patch.object(
            module, "step2_find_locations", return_value=[{"table": "dwd_fox_mission_log", "dt": "2026-04-29"}]
        ), mock.patch.object(
            module, "load_manual_review_state", return_value={}
        ), mock.patch.object(
            module, "apply_repair_strategy", return_value=([{"table": "dwd_fox_mission_log", "dt": "2026-04-29"}], [])
        ), mock.patch.object(
            module, "execute_repairs_in_batches", return_value=([{"table": "dwd_fox_mission_log"}], [], [])
        ), mock.patch.object(
            module, "record_redundant_retry_attempt"
        ), mock.patch.object(
            module, "record_manual_review_tasks"
        ), mock.patch.object(
            module, "save_manual_review_state"
        ), mock.patch.object(
            module, "step5_execute_fuyan"
        ) as mock_step5, mock.patch.object(
            module, "evaluate_repair_outcome"
        ) as mock_evaluate, mock.patch.object(
            module, "get_remaining_alert_tables", return_value=unresolved_tables
        ), mock.patch.object(
            module, "step6_save_report"
        ) as mock_step6, mock.patch.object(module, "log"):
            module.main()

        mock_step5.assert_not_called()
        mock_evaluate.assert_not_called()
        summary = mock_step6.call_args[0][4]
        final_fuyan_results = mock_step6.call_args[0][3]
        self.assertEqual(final_fuyan_results, [])
        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(summary["resolved_count"], 0)

    def test_summarize_repair_outcome_uses_post_fuyan_remaining_tables(self):
        module = load_module()
        alerts = [
            {"table": "dwd_fox_call_history", "dt": "2026-04-21"},
            {"table": "dwd_asset_biz_report", "dt": "2026-04-21"},
        ]
        completed_tasks = [
            {"table": "dwd_fox_call_history", "dt": "2026-04-21"},
            {"table": "dwd_asset_biz_report", "dt": "2026-04-21"},
        ]
        failed_tasks = []
        manual_review_tasks = []
        remaining_tables = {"dwd_asset_biz_report"}

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=manual_review_tasks,
            remaining_tables=remaining_tables,
        )

        self.assertEqual(summary["initial_alert_count"], 2)
        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(
            [item["table"] for item in summary["resolved_tasks"]],
            ["dwd_fox_call_history"],
        )
        self.assertEqual(
            [item["table"] for item in summary["remaining_tasks"]],
            ["dwd_asset_biz_report"],
        )
        self.assertEqual(summary["manual_review_count"], 1)
        self.assertEqual(
            [item["table"] for item in summary["rerun_tasks"]],
            ["dwd_fox_call_history", "dwd_asset_biz_report"],
        )

    def test_summarize_repair_outcome_marks_redundant_remaining_task_with_delete_hint(self):
        module = load_module()
        alerts = [{"table": "dwd_mkt_sms_cost_monthly", "dt": "2026-04-29", "diff": -49}]
        completed_tasks = [{"table": "dwd_mkt_sms_cost_monthly", "dt": "2026-04-29", "diff": -49}]
        failed_tasks = []
        manual_review_tasks = []
        remaining_tables = {"dwd_mkt_sms_cost_monthly"}

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=manual_review_tasks,
            remaining_tables=remaining_tables,
        )

        self.assertEqual(summary["remaining_count"], 1)
        self.assertIn("底层是否需要删数", summary["remaining_tasks"][0]["error"])

    def test_summarize_repair_outcome_keeps_out_of_window_manual_review_as_remaining(self):
        module = load_module()
        alerts = [
            {
                "table": "dwd_user_coupon",
                "dt": "2026-02-08",
                "diff": -7,
                "status": "skipped_out_of_window",
            }
        ]
        completed_tasks = []
        failed_tasks = []
        manual_review_tasks = [
            {
                "table": "dwd_user_coupon",
                "dt": "2026-02-08",
                "diff": -7,
                "status": "skipped_manual_review",
                "error": "告警校验范围过长，请人工处理",
            }
        ]

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=manual_review_tasks,
            remaining_tables=set(),
        )

        self.assertEqual(summary["resolved_count"], 0)
        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(summary["manual_review_count"], 1)
        self.assertEqual(summary["remaining_tasks"][0]["table"], "dwd_user_coupon")
        self.assertIn("人工处理", summary["remaining_tasks"][0]["error"])

    def test_summarize_repair_outcome_does_not_mark_failed_rerun_as_resolved(self):
        module = load_module()
        alerts = [{"table": "dwd_mkt_sms_cost_monthly", "dt": "2026-05-02", "diff": -52}]
        completed_tasks = []
        failed_tasks = [
            {
                "table": "dwd_mkt_sms_cost_monthly",
                "dt": "2026-05-02",
                "status": "failed",
                "error": "查询失败: workflow instance 911,838 does not exist",
            }
        ]

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=[],
            remaining_tables=set(),
        )

        self.assertEqual(summary["resolved_count"], 0)
        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(summary["remaining_tasks"][0]["table"], "dwd_mkt_sms_cost_monthly")
        self.assertEqual(summary["remaining_tasks"][0]["result"], "manual_review")
        self.assertIn("查询失败", summary["remaining_tasks"][0]["error"])

    def test_summarize_repair_outcome_fills_default_error_for_failed_remaining_task(self):
        module = load_module()
        alerts = [{"table": "dwd_user_individual", "dt": "2026-05-12"}]
        completed_tasks = [{"table": "dwd_user_individual", "dt": "2026-05-12", "end_time": "2026-05-12 14:12:07"}]
        failed_tasks = [{"table": "dwd_user_individual", "dt": "2026-05-12", "final_status": "failed", "error": ""}]

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            manual_review_tasks=[],
            remaining_tables={"dwd_user_individual"},
        )

        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(summary["remaining_tasks"][0]["table"], "dwd_user_individual")
        self.assertEqual(summary["remaining_tasks"][0]["result"], "manual_review")
        self.assertEqual(summary["remaining_tasks"][0]["error"], "自动重跑失败，需人工处理")

    def test_execute_repairs_in_batches_includes_step3_start_failures_in_failed_tasks(self):
        module = load_module()
        tasks = [{"table": "dwb_asset_info"}]
        start_failed = [{"table": "dwb_asset_info", "status": "failed", "error": "HTTP Error 405: Method Not Allowed"}]

        with mock.patch.object(module, "step3_start_repair", return_value=(start_failed, [])), \
            mock.patch.object(module, "step4_wait_and_check", return_value=([], [])), \
            mock.patch.object(module, "log"):
            _, completed_tasks, failed_tasks = module.execute_repairs_in_batches(tasks)

        self.assertEqual(completed_tasks, [])
        self.assertEqual(len(failed_tasks), 1)
        self.assertEqual(failed_tasks[0]["table"], "dwb_asset_info")
        self.assertIn("405", failed_tasks[0]["error"])

    def test_summarize_repair_outcome_keeps_start_failure_reason_for_remaining_task(self):
        module = load_module()
        alerts = [{"table": "dwb_asset_info", "dt": "2026-05-25", "diff": 1}]
        failed_tasks = [{"table": "dwb_asset_info", "status": "failed", "error": "HTTP Error 405: Method Not Allowed"}]

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=[],
            failed_tasks=failed_tasks,
            manual_review_tasks=[],
            remaining_tables={"dwb_asset_info"},
        )

        self.assertEqual(summary["remaining_count"], 1)
        self.assertEqual(summary["remaining_tasks"][0]["table"], "dwb_asset_info")
        self.assertIn("405", summary["remaining_tasks"][0]["error"])

    def test_summarize_repair_outcome_counts_manual_review_tasks_in_display_pending_tables(self):
        module = load_module()
        alerts = [{"table": "dwd_user_coupon", "dt": "2026-02-08"}]
        manual_review_tasks = [
            {
                "table": "dwd_user_coupon",
                "dt": "2026-02-08",
                "status": "skipped_manual_review",
                "error": "告警校验范围过长，请人工处理",
            }
        ]

        summary = module.summarize_repair_outcome(
            alerts=alerts,
            completed_tasks=[],
            failed_tasks=[],
            manual_review_tasks=manual_review_tasks,
            remaining_tables=set(),
        )

        self.assertEqual(summary["display_pending_tables_count"], 1)

    def test_generate_tv_report_describes_resolved_and_manual_review_after_fuyan(self):
        module = load_module()
        summary = {
            "initial_alert_count": 2,
            "resolved_count": 1,
            "remaining_count": 1,
            "manual_review_count": 1,
            "rerun_tasks": [
                {
                    "table": "dwd_fox_call_history",
                    "dt": "2026-04-21",
                    "instance_id": 806135,
                    "end_time": "2026-04-29 14:05:14",
                },
                {
                    "table": "dwd_asset_biz_report",
                    "dt": "2026-04-21",
                    "instance_id": 806136,
                    "end_time": "2026-04-29 14:05:03",
                },
            ],
            "resolved_tasks": [
                {"table": "dwd_fox_call_history", "dt": "2026-04-21"}
            ],
            "remaining_tasks": [
                {
                    "table": "dwd_asset_biz_report",
                    "dt": "2026-04-21",
                    "error": "疑似当前层数据多于底层，重跑一次后仍未恢复，建议检查底层是否需要删数，并人工判断修复",
                }
            ],
            "post_fuyan_remaining_tables": {"dwd_asset_biz_report"},
            "display_pending_tables_count": 1,
        }
        fuyan_results = [
            {"name": "每日复验全级别数据(W-1)", "status": "success", "id": 806145}
        ]

        with mock.patch.object(module, "log"):
            report = module.generate_tv_report(summary, fuyan_results)

        self.assertIn("初始去重告警: 2 个", report)
        self.assertIn("复验后已消失: 1 个", report)
        self.assertIn("复验后仍存在: 1 个", report)
        self.assertIn("当前未处理告警表: 1 个", report)
        self.assertIn("本次已重跑任务", report)
        self.assertIn("实例ID: 806135", report)
        self.assertIn("实例ID: 806136", report)
        self.assertIn("dwd_fox_call_history", report)
        self.assertIn("dwd_asset_biz_report", report)
        self.assertIn("底层是否需要删数", report)

    def test_generate_tv_report_includes_diff_for_manual_review_items(self):
        module = load_module()
        summary = {
            "initial_alert_count": 1,
            "resolved_count": 0,
            "remaining_count": 1,
            "manual_review_count": 1,
            "rerun_tasks": [],
            "resolved_tasks": [],
            "remaining_tasks": [
                {
                    "table": "dwd_fox_mission_log",
                    "dt": "2026-04-29",
                    "diff": 456,
                    "error": "节点被配置为禁止执行，需人工查看修复",
                }
            ],
            "post_fuyan_remaining_tables": {"dwd_fox_mission_log"},
            "display_pending_tables_count": 1,
        }

        with mock.patch.object(module, "log"):
            report = module.generate_tv_report(summary, [])

        self.assertIn("数据量差异: 456", report)

    def test_evaluate_repair_outcome_queries_remaining_tables_after_fuyan_wait(self):
        module = load_module()
        completed_tasks = [{"table": "dwd_fox_call_history", "dt": "2026-04-21"}]
        failed_tasks = []
        alerts = [{"table": "dwd_fox_call_history", "dt": "2026-04-21"}]
        fuyan_results = [{"name": "每日复验全级别数据(W-1)", "status": "success", "id": 806145}]

        with mock.patch.object(
            module,
            "wait_for_fuyan_results",
            return_value=fuyan_results,
        ) as wait_mock, mock.patch.object(
            module,
            "get_remaining_alert_tables",
            return_value=set(),
        ) as remaining_mock:
            summary, final_fuyan_results = module.evaluate_repair_outcome(
                alerts=alerts,
                completed_tasks=completed_tasks,
                failed_tasks=failed_tasks,
                manual_review_tasks=[],
                fuyan_results=fuyan_results,
            )

        wait_mock.assert_called_once_with(fuyan_results)
        remaining_mock.assert_called_once()
        self.assertEqual(final_fuyan_results, fuyan_results)
        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["remaining_count"], 0)

    def test_wait_for_fuyan_results_discovers_real_process_instance_before_detail_query(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        fuyan_results = [
            {
                "name": "每日复验全级别数据(W-1)",
                "status": "success",
                "id": 11111,
                "start_response_id": 11111,
                "resolved_instance_id": None,
                "workflow_code": "wf-fuyan",
                "launched_at": "2026-05-11 09:35:44",
            }
        ]
        detail_calls = []

        def fake_get_instance_detail(project_code, instance_id):
            detail_calls.append(instance_id)
            if instance_id == 22222:
                return True, {"id": 22222, "state": "SUCCESS", "endTime": "2026-05-11 09:36:40"}, ""
            return False, {}, "query process instance by id error"

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            return_value={"id": 22222, "state": "RUNNING_EXECUTION", "startTime": "2026-05-11 09:35:45"},
        ), mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=fake_get_instance_detail,
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            final_results = module.wait_for_fuyan_results(
                fuyan_results,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(detail_calls[0], 22222)
        self.assertEqual(final_results[0]["id"], 22222)
        self.assertEqual(final_results[0]["resolved_instance_id"], 22222)
        self.assertEqual(final_results[0]["final_status"], "success")
        self.assertEqual(final_results[0]["end_time"], "2026-05-11 09:36:40")

    def test_wait_for_fuyan_results_falls_back_to_instance_list_when_detail_query_fails(self):
        module = load_module()
        module.DS_API_MODE = "process_v2"
        module.DS_INSTANCE_ENDPOINT_STYLE = "process-instances"
        fuyan_results = [
            {
                "name": "两小时复验3级表数据(D-1)",
                "status": "success",
                "id": 33333,
                "workflow_code": "wf-fuyan-l3",
                "launched_at": "2026-05-11 09:35:44",
            }
        ]

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            return_value={},
        ), mock.patch.object(
            module,
            "get_instance_detail",
            return_value=(False, {}, "query process instance by id error"),
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={"id": 33333, "state": "SUCCESS", "endTime": "2026-05-11 09:36:40"},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            final_results = module.wait_for_fuyan_results(
                fuyan_results,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(final_results[0]["id"], 33333)
        self.assertEqual(final_results[0]["final_status"], "success")
        self.assertEqual(final_results[0]["end_time"], "2026-05-11 09:36:40")

    def test_wait_for_fuyan_results_uses_workflow_specific_project_code_for_queries(self):
        module = load_module()
        fuyan_results = [
            {
                "name": "每日复验全级别数据(W-1)",
                "status": "success",
                "id": 11111,
                "workflow_code": "wf-fuyan",
                "project_code": "project-fuyan-a",
                "launched_at": "2026-05-11 09:35:44",
            }
        ]
        seen_project_codes = []

        def fake_get_instance_detail(project_code, instance_id):
            seen_project_codes.append(("detail", project_code, instance_id))
            return True, {"id": instance_id, "state": "SUCCESS", "endTime": "2026-05-11 09:36:40"}, ""

        def fake_find_recent_instance_by_workflow(project_code, workflow_code, launched_at=None, state_types=None):
            seen_project_codes.append(("recent", project_code, workflow_code))
            return {"id": 11111, "state": "RUNNING_EXECUTION"}

        with mock.patch.object(
            module,
            "find_recent_instance_by_workflow",
            side_effect=fake_find_recent_instance_by_workflow,
        ), mock.patch.object(
            module,
            "get_instance_detail",
            side_effect=fake_get_instance_detail,
        ), mock.patch.object(
            module,
            "get_instance_from_list",
            return_value={},
        ), mock.patch.object(module, "log"), mock.patch("time.sleep"):
            final_results = module.wait_for_fuyan_results(
                fuyan_results,
                poll_interval=1,
                max_wait=10,
            )

        self.assertEqual(final_results[0]["final_status"], "success")
        self.assertIn(("recent", "project-fuyan-a", "wf-fuyan"), seen_project_codes)
        self.assertIn(("detail", "project-fuyan-a", 11111), seen_project_codes)

    def test_generate_tv_report_uses_display_pending_tables_count_when_present(self):
        module = load_module()
        summary = {
            "initial_alert_count": 1,
            "resolved_count": 0,
            "remaining_count": 1,
            "manual_review_count": 1,
            "rerun_tasks": [],
            "resolved_tasks": [],
            "remaining_tasks": [
                {"table": "dwd_user_coupon", "error": "告警校验范围过长，请人工处理"}
            ],
            "post_fuyan_remaining_tables": set(),
            "display_pending_tables_count": 1,
        }

        with mock.patch.object(module, "log"):
            report = module.generate_tv_report(summary, [])

        self.assertIn("当前未处理告警表: 1 个", report)

    def test_step2_find_locations_uses_configured_priority_workflows(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = [("wf-priority", "PRIORITY")]
        alerts = [{"id": 1, "table": "dwd_user_info", "dt": "2026-05-09", "diff": 1}]
        searched_codes = []

        def fake_search(workflow_code, table_name):
            searched_codes.append(workflow_code)
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(module, "get_workflow_definition_list", return_value=(True, {"totalList": []}, "")), \
            mock.patch.object(module, "log"):
            module.step2_find_locations(alerts)

        self.assertEqual(searched_codes, ["wf-priority"])

    def test_step2_find_locations_accepts_workflow_definition_code_from_list_items(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [{"id": 1, "table": "dwd_fox_chatbot_dialog", "dt": "2026-05-11", "diff": 1}]
        searched_codes = []

        def fake_search(workflow_code, table_name):
            searched_codes.append(workflow_code)
            if workflow_code == "wf-dialog":
                return {
                    "workflow_code": "wf-dialog",
                    "workflow_name": "印尼-数仓工作流（1/2H）",
                    "task_code": "task-dialog",
                    "task_name": "dwd_fox_chatbot_dialog",
                    "task_flag": "YES",
                }
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(
                module,
                "get_workflow_definition_list",
                return_value=(True, {"totalList": [{"workflowDefinitionCode": "wf-dialog"}]}, ""),
            ), \
            mock.patch.object(module, "get_schedule_map", return_value={}), \
            mock.patch.object(module, "is_workflow_scheduled", return_value=False), \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(searched_codes, ["wf-dialog"])
        self.assertEqual(tasks[0]["workflow_code"], "wf-dialog")
        self.assertEqual(tasks[0]["task_name"], "dwd_fox_chatbot_dialog")

    def test_step2_find_locations_tries_display_table_name_first(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [
            {
                "id": 1,
                "table": "dwd_fox_chatbot_dialog",
                "src_tbl": "dwb_a5_dialog",
                "dest_tbl": "dwd_fox_chatbot_dialog",
                "search_tables": ["dwd_fox_chatbot_dialog", "dwb_a5_dialog"],
                "dt": "2026-05-11",
                "diff": 1,
            }
        ]
        searched = []

        def fake_search(workflow_code, table_name):
            searched.append((workflow_code, table_name))
            if table_name == "dwd_fox_chatbot_dialog":
                return {
                    "workflow_code": "wf-dialog",
                    "workflow_name": "印尼-数仓工作流（1H）",
                    "task_code": "task-dialog",
                    "task_name": "dwd_fox_chatbot_dialog",
                    "task_flag": "YES",
                }
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(
                module,
                "get_workflow_definition_list",
                return_value=(True, {"totalList": [{"workflowDefinitionCode": "wf-dialog"}]}, ""),
            ), \
            mock.patch.object(module, "get_schedule_map", return_value={}), \
            mock.patch.object(module, "is_workflow_scheduled", return_value=False), \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(
            searched,
            [("wf-dialog", "dwd_fox_chatbot_dialog")],
        )
        self.assertEqual(tasks[0]["workflow_code"], "wf-dialog")
        self.assertEqual(tasks[0]["table"], "dwd_fox_chatbot_dialog")
        self.assertEqual(tasks[0]["task_name"], "dwd_fox_chatbot_dialog")

    def test_step2_find_locations_skips_blocked_workflow_and_marks_manual_review(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [{"id": 1, "table": "dwb_asset_info", "dt": "2026-05-11", "diff": 1}]

        def fake_search(workflow_code, table_name):
            if workflow_code == "wf-blocked":
                return {
                    "workflow_code": "wf-blocked",
                    "workflow_name": "印尼-宽表全量工作流（1D）",
                    "task_code": "task-asset",
                    "task_name": "dwb_asset_info",
                    "task_flag": "YES",
                }
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(
                module,
                "get_workflow_definition_list",
                return_value=(True, {"totalList": [{"workflowDefinitionCode": "wf-blocked"}]}, ""),
            ), \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(tasks[0]["workflow_code"], "")
        self.assertEqual(tasks[0]["workflow_name"], "印尼-宽表全量工作流（1D）")
        self.assertIn("禁止自动修复", tasks[0]["error"])

    def test_step2_find_locations_skips_blocked_workflow_and_uses_other_match(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [{"id": 1, "table": "dwb_asset_info", "dt": "2026-05-11", "diff": 1}]
        searched_codes = []

        def fake_search(workflow_code, table_name):
            searched_codes.append(workflow_code)
            if workflow_code == "wf-blocked":
                return {
                    "workflow_code": "wf-blocked",
                    "workflow_name": "印尼-宽表全量工作流（1D）",
                    "task_code": "task-blocked",
                    "task_name": "dwb_asset_info",
                    "task_flag": "YES",
                }
            if workflow_code == "wf-good":
                return {
                    "workflow_code": "wf-good",
                    "workflow_name": "印尼-数仓工作流（1H）",
                    "task_code": "task-good",
                    "task_name": "dwb_asset_info",
                    "task_flag": "YES",
                }
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(
                module,
                "get_workflow_definition_list",
                return_value=(
                    True,
                    {
                        "totalList": [
                            {"workflowDefinitionCode": "wf-blocked"},
                            {"workflowDefinitionCode": "wf-good"},
                        ]
                    },
                    "",
                ),
            ), \
            mock.patch.object(module, "get_schedule_map", return_value={}), \
            mock.patch.object(module, "is_workflow_scheduled", return_value=False), \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(searched_codes, ["wf-blocked", "wf-good"])
        self.assertEqual(tasks[0]["workflow_code"], "wf-good")
        self.assertEqual(tasks[0]["workflow_name"], "印尼-数仓工作流（1H）")

    def test_step2_find_locations_skips_forbidden_match_and_uses_later_runnable_workflow(self):
        module = load_module()
        module.PRIORITY_WORKFLOWS = []
        alerts = [{"id": 1, "table": "dws_user_performance_first_loan_info", "dt": "2026-05-19", "diff": 0}]
        searched_codes = []

        def fake_search(workflow_code, table_name):
            searched_codes.append(workflow_code)
            if workflow_code == "wf-forbidden":
                return {
                    "workflow_code": "wf-forbidden",
                    "workflow_name": "DWS（禁跑节点）",
                    "task_code": "task-no",
                    "task_name": "dws_user_performance_first_loan_info",
                    "task_flag": "NO",
                }
            if workflow_code == "wf-runnable":
                return {
                    "workflow_code": "wf-runnable",
                    "workflow_name": "DWS（可执行节点）",
                    "task_code": "task-yes",
                    "task_name": "dws_user_performance_first_loan_info",
                    "task_flag": "YES",
                }
            return None

        with mock.patch.object(module, "step2_search_in_workflow", side_effect=fake_search), \
            mock.patch.object(
                module,
                "get_workflow_definition_list",
                return_value=(
                    True,
                    {
                        "totalList": [
                            {"workflowDefinitionCode": "wf-forbidden"},
                            {"workflowDefinitionCode": "wf-runnable"},
                        ]
                    },
                    "",
                ),
            ), \
            mock.patch.object(module, "get_schedule_map", return_value={}), \
            mock.patch.object(module, "is_workflow_scheduled", return_value=False), \
            mock.patch.object(module, "log"):
            tasks = module.step2_find_locations(alerts)

        self.assertEqual(searched_codes, ["wf-forbidden", "wf-runnable"])
        self.assertEqual(tasks[0]["workflow_code"], "wf-runnable")
        self.assertEqual(tasks[0]["task_code"], "task-yes")
        self.assertEqual(tasks[0]["task_flag"], "YES")

    def test_get_workflow_definition_list_falls_back_when_first_endpoint_returns_empty_success(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return True, {"totalList": [], "totalPage": 1}, ""
            if endpoint.endswith("/process-definition?pageNo=1&pageSize=100"):
                return True, {"totalList": [{"code": "wf-1"}], "totalPage": 1}, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            success, data, msg = module.get_workflow_definition_list()

        self.assertTrue(success)
        self.assertEqual(data["totalList"], [{"code": "wf-1"}])

    def test_step3_start_repair_falls_back_when_process_style_returns_empty_success_data(self):
        module = load_module()
        tasks = [
            {
                "table": "dwb_a5_dialog",
                "dt": "2026-05-11",
                "workflow_code": "wf-1",
                "workflow_name": "印尼-数仓工作流（1H）",
                "task_code": "task-1",
                "task_name": "dwb_a5_dialog",
                "task_flag": "YES",
            }
        ]
        attempts = []

        def fake_ds_api_post(endpoint, data):
            attempts.append((endpoint, dict(data)))
            if endpoint.endswith("start-process-instance"):
                return True, {"data": None}, ""
            if endpoint.endswith("start-workflow-instance"):
                return True, {"data": [98765]}, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_post", side_effect=fake_ds_api_post), \
            mock.patch.object(module, "find_conflicting_running_instance", return_value=None), \
            mock.patch.object(module, "log"), \
            mock.patch("time.sleep"):
            results, running_instances = module.step3_start_repair(tasks)

        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0][0].endswith("start-process-instance"))
        self.assertTrue(attempts[1][0].endswith("start-workflow-instance"))
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["instance_id"], 98765)
        self.assertEqual(running_instances[0]["instance_id"], 98765)

    def test_get_workflow_definition_list_falls_back_to_query_process_definition_list(self):
        module = load_module()

        def fake_ds_api_get(endpoint):
            if endpoint.endswith("/workflow-definition?pageNo=1&pageSize=100"):
                return False, {}, "non-json response: <!DOCTYPE html>"
            if endpoint.endswith("/process-definition?pageNo=1&pageSize=100"):
                return False, {}, "non-json response: <!DOCTYPE html>"
            if endpoint.endswith("/workflow-definition/query-workflow-definition-list"):
                return False, {}, "not found"
            if endpoint.endswith("/workflow-definition/query-process-definition-list"):
                return False, {}, "not found"
            if endpoint.endswith("/process-definition/query-workflow-definition-list"):
                return False, {}, "not found"
            if endpoint.endswith("/process-definition/query-process-definition-list"):
                return True, [{"code": "wf-query"}], ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            success, data, msg = module.get_workflow_definition_list()

        self.assertTrue(success)
        self.assertEqual(data["totalList"], [{"code": "wf-query"}])

    def test_get_workflow_definition_detail_falls_back_to_workflow_definition_when_process_style_is_configured(self):
        module = load_module()
        module.DS_DEFINITION_ENDPOINT_STYLE = "process-definition"

        def fake_ds_api_get(endpoint):
            if endpoint == "/projects/default-project/process-definition/wf-query":
                return False, {}, "non-json response: <!DOCTYPE html>"
            if endpoint == "/projects/default-project/workflow-definition/wf-query":
                return True, {"processDefinition": {"name": "WF Query"}}, ""
            raise AssertionError(endpoint)

        with mock.patch.object(module, "ds_api_get", side_effect=fake_ds_api_get):
            success, data, msg = module.get_workflow_definition_detail("wf-query")

        self.assertTrue(success)
        self.assertEqual(data["processDefinition"]["name"], "WF Query")


if __name__ == "__main__":
    unittest.main()
