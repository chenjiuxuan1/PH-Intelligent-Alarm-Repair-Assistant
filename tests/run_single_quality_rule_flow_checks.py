import importlib.util
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


if __name__ == "__main__":
    unittest.main()
