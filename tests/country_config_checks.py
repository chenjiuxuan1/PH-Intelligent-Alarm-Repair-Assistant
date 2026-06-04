import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "config" / "config.py"


def load_module():
    spec = importlib.util.spec_from_file_location("runtime_config", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CountryConfigTests(unittest.TestCase):
    def test_ds_config_defaults_to_ph_ds34_start_with_auto_query_fallbacks(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            module = load_module()

        self.assertEqual(module.DS_CONFIG["api_mode"], "process_v2")
        self.assertEqual(module.DS_CONFIG["start_endpoint"], "start-process-instance")
        self.assertEqual(module.DS_CONFIG["start_code_field"], "processDefinitionCode")
        self.assertEqual(module.DS_CONFIG["definition_endpoint_style"], "auto")
        self.assertEqual(module.DS_CONFIG["instance_endpoint_style"], "auto")

    def test_local_env_file_populates_missing_environment_values(self):
        env = {"APP_ENV_FILE": "/tmp/ine-local.env"}
        file_content = "\n".join(
            [
                "DS_BASE_URL=http://id.local:12345/dolphinscheduler",
                "DS_TOKEN=token-from-file",
                "DB_PASSWORD=db-pass-from-file",
            ]
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("os.path.exists", side_effect=lambda path: path == "/tmp/ine-local.env"):
                with mock.patch("builtins.open", mock.mock_open(read_data=file_content)):
                    module = load_module()

        self.assertEqual(module.DS_CONFIG["base_url"], "http://id.local:12345/dolphinscheduler")
        self.assertEqual(module.DS_CONFIG["token"], "token-from-file")
        self.assertEqual(module.DB_CONFIG["password"], "db-pass-from-file")

    def test_ds_config_reads_main_runtime_values_from_environment(self):
        env = {
            "DS_BASE_URL": "https://id.example.com/dolphinscheduler",
            "DS_TOKEN": "token-id",
            "DS_PROJECT_CODE": "2001",
            "DS_FUYAN_PROJECT_CODE": "3001",
            "DS_ENVIRONMENT_CODE": "4001",
            "DS_TENANT_CODE": "tenant_id",
            "DS_API_MODE": "process_v2",
            "DS_START_ENDPOINT": "start-process-instance",
            "DS_START_CODE_FIELD": "processDefinitionCode",
            "DS_DEFINITION_ENDPOINT_STYLE": "process-definition",
            "DS_INSTANCE_ENDPOINT_STYLE": "process-instances",
            "PRIORITY_WORKFLOW_CODES_JSON": json.dumps([["wf-a", "WF_A"]]),
        }

        with mock.patch.dict(os.environ, env, clear=False):
            module = load_module()

        self.assertEqual(module.DS_CONFIG["base_url"], "https://id.example.com/dolphinscheduler")
        self.assertEqual(module.DS_CONFIG["token"], "token-id")
        self.assertEqual(module.DS_CONFIG["project_code"], "2001")
        self.assertEqual(module.DS_CONFIG["fuyan_project_code"], "3001")
        self.assertEqual(module.DS_CONFIG["environment_code"], "4001")
        self.assertEqual(module.DS_CONFIG["tenant_code"], "tenant_id")
        self.assertEqual(module.DS_CONFIG["api_mode"], "process_v2")
        self.assertEqual(module.DS_CONFIG["start_endpoint"], "start-process-instance")
        self.assertEqual(module.DS_CONFIG["start_code_field"], "processDefinitionCode")
        self.assertEqual(module.DS_CONFIG["definition_endpoint_style"], "process-definition")
        self.assertEqual(module.DS_CONFIG["instance_endpoint_style"], "process-instances")
        self.assertEqual(module.REPAIR_CONFIG["priority_workflow_codes"], [["wf-a", "WF_A"]])

    def test_fuyan_workflows_can_be_overridden_by_json_environment_variable(self):
        workflows = [
            {
                "name": "印尼每日复验",
                "code": "wf-1",
                "level": "all",
                "project_code": "pj-1",
                "workflow_name": "印尼每日复验",
            }
        ]
        env = {"FUYAN_WORKFLOWS_JSON": json.dumps(workflows, ensure_ascii=False)}

        with mock.patch.dict(os.environ, env, clear=False):
            module = load_module()

        self.assertEqual(module.FUYAN_WORKFLOWS, workflows)

    def test_table_config_reads_alert_and_result_table_names_from_environment(self):
        env = {
            "QUALITY_RESULT_TABLE": "indo_quality_result",
            "QUALITY_ALERT_TABLE": "indo_quality_alert",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            module = load_module()

        self.assertEqual(module.TABLE_CONFIG["quality_result_table"], "indo_quality_result")
        self.assertEqual(module.TABLE_CONFIG["quality_alert_table"], "indo_quality_alert")

    def test_workspace_config_uses_runtime_override_for_root_and_state_paths(self):
        env = {"APP_WORKSPACE": "/srv/ine-repair"}

        with mock.patch.dict(os.environ, env, clear=False):
            module = load_module()

        self.assertEqual(module.WORKSPACE_CONFIG["root"], "/srv/ine-repair")
        self.assertTrue(module.WORKSPACE_CONFIG["manual_review_state_file"].startswith("/srv/ine-repair/"))
        self.assertTrue(module.WORKSPACE_CONFIG["auto_repair_records_dir"].startswith("/srv/ine-repair/"))
        self.assertTrue(module.WORKSPACE_CONFIG["quality_rule_backlog_file"].startswith("/srv/ine-repair/"))

    def test_quality_rule_form_config_reads_runtime_values(self):
        env = {
            "QUALITY_RULE_FORM_COUNTRY": "ph",
            "QUALITY_RULE_FORM_VIEW_URL": "https://docs.google.com/forms/d/e/viewform",
            "QUALITY_RULE_FORM_POST_URL": "https://docs.google.com/forms/d/e/formResponse",
            "QUALITY_RULE_FORM_FIELD_MAP_JSON": json.dumps({"candidate_key": "entry.123"}),
            "QUALITY_RULE_CONFIRMATION_EXPORT_URL": "https://docs.google.com/spreadsheets/d/e/export?format=csv",
            "QUALITY_RULE_CONFIRMATION_COLUMN_MAP_JSON": json.dumps({"candidate_key": "候选键", "need_apply": "是否补充"}),
            "QUALITY_RULE_NOTIFY_BOT_ID": "08826b39-e6eb-44fb-9c25-9778a8171f49",
            "QUALITY_RULE_NOTIFY_MENTIONS": "a@example.com,b@example.com",
            "QUALITY_GIT_SCAN_ROOTS": "/data/git,/srv/git",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            module = load_module()

        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["country"], "ph")
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["view_url"], env["QUALITY_RULE_FORM_VIEW_URL"])
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["post_url"], env["QUALITY_RULE_FORM_POST_URL"])
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["field_map"], {"candidate_key": "entry.123"})
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["confirmation_export_url"], env["QUALITY_RULE_CONFIRMATION_EXPORT_URL"])
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["confirmation_column_map"]["need_apply"], "是否补充")
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["notify_bot_id"], env["QUALITY_RULE_NOTIFY_BOT_ID"])
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["notify_mentions"], ["a@example.com", "b@example.com"])
        self.assertEqual(module.QUALITY_RULE_FORM_CONFIG["git_scan_roots"], ["/data/git", "/srv/git"])


if __name__ == "__main__":
    unittest.main()
