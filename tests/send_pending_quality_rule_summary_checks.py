import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "send_pending_quality_rule_summary.py"


def load_module():
    fake_config = types.ModuleType("config")
    fake_config_config = types.ModuleType("config.config")
    fake_config_config.QUALITY_RULE_FORM_CONFIG = {
        "country": "ph",
        "confirmation_sheet_url": "https://docs.google.com/spreadsheets/d/test/edit#gid=1",
    }
    fake_confirmation = types.ModuleType("core.quality_rule_confirmation")
    fake_confirmation.load_backlog = mock.MagicMock()
    fake_confirmation.notify_new_candidates_via_tv = mock.MagicMock(return_value={"success": True, "status_code": 202})
    fake_confirmation.save_backlog = mock.MagicMock()

    previous_config = sys.modules.get("config")
    previous_config_config = sys.modules.get("config.config")
    previous_confirmation = sys.modules.get("core.quality_rule_confirmation")
    sys.modules["config"] = fake_config
    sys.modules["config.config"] = fake_config_config
    sys.modules["core.quality_rule_confirmation"] = fake_confirmation
    try:
        spec = importlib.util.spec_from_file_location("send_pending_quality_rule_summary", str(MODULE_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, fake_confirmation
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


class SendPendingQualityRuleSummaryTests(unittest.TestCase):
    def test_collect_items_filters_missing_and_non_pending_items(self):
        module, _ = load_module()
        backlog = {
            "items": {
                "a": {"candidate_key": "a", "status": "pending_confirmation"},
                "b": {"candidate_key": "b", "status": "existing"},
            }
        }

        items = module.collect_items(backlog, ["a", "b", "c"])

        self.assertEqual([item["candidate_key"] for item in items], ["a"])

    def test_main_sends_one_summary_and_marks_notified(self):
        module, fake_confirmation = load_module()
        backlog = {
            "items": {
                "a": {"candidate_key": "a", "status": "pending_confirmation", "dest_tbl": "foo"},
                "b": {"candidate_key": "b", "status": "pending_confirmation", "dest_tbl": "bar"},
            }
        }
        fake_confirmation.load_backlog.return_value = backlog

        argv_backup = sys.argv
        stdout_backup = sys.stdout
        sys.argv = [
            "send_pending_quality_rule_summary.py",
            "--candidate-key",
            "a",
            "--candidate-key",
            "b",
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
        fake_confirmation.notify_new_candidates_via_tv.assert_called_once()
        fake_confirmation.save_backlog.assert_called_once()
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["notified_candidates"], 2)
        self.assertEqual(payload["notified_candidate_keys"], ["a", "b"])
        self.assertTrue(backlog["items"]["a"]["notified_at"])
        self.assertTrue(backlog["items"]["b"]["notified_at"])


if __name__ == "__main__":
    unittest.main()
