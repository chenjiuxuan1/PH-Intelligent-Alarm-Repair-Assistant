import json
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import quality_rule_ai_helper as module


class QualityRuleAiHelperTests(unittest.TestCase):
    def test_build_ai_messages_uses_compact_payload(self):
        messages = module.build_ai_messages(
            "dwd_sec",
            {
                "db": "dwd_sec",
                "tbl": "dwd_cst_pay_cost_detail",
                "src_db": "ods",
                "src_tbl": "ods_repay_cpop_income_item",
                "columns": ["create_at"],
                "monitor_level": 3,
            },
            [{"path": "/tmp/example.sql", "snippet": "select * from x where create_at > now()"}],
            "src_check_field/dest_check_field 不一致",
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["task"], "generate_metric_rule_candidate")
        self.assertEqual(payload["dest_tbl"], "dwd_cst_pay_cost_detail")
        self.assertEqual(payload["src_tbl"], "ods_repay_cpop_income_item")
        self.assertEqual(payload["requested_metric_field"], "")
        self.assertEqual(payload["source_columns"], ["create_at"])
        self.assertEqual(payload["dest_columns"], [])
        self.assertNotIn("table", payload)
        self.assertIn("主驱动源表", messages[0]["content"])
        self.assertIn("ods_paysvr_fee", messages[1]["content"])
        self.assertIn("SUM(total_cost)", messages[1]["content"])
        self.assertIn("ROUND(SUM(total_cost), 6)", messages[1]["content"])

    def test_build_ai_messages_includes_requested_metric_field_when_present(self):
        messages = module.build_ai_messages(
            "dwd_sec",
            {
                "db": "dwd_sec",
                "tbl": "dwd_cst_pay_cost_detail",
                "src_db": "ods",
                "src_tbl": "ods_paysvr_fee",
                "requested_metric_field": "total_cost",
                "columns": ["fee_finish_at", "fee_amount"],
                "dest_columns": ["fee_finish_at", "total_cost"],
            },
            [],
            "用户指定字段",
        )

        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["requested_metric_field"], "total_cost")

    def test_deadline_and_timeouts_default_to_disabled(self):
        with mock.patch.dict(module.os.environ, {}, clear=False):
            self.assertIsNone(module._ai_deadline_seconds())
            self.assertIsNone(module._optional_timeout_seconds("QUALITY_RULE_AI_SDK_TIMEOUT_SECONDS"))
            self.assertIsNone(module._optional_timeout_seconds("QUALITY_RULE_AI_HTTP_TIMEOUT_SECONDS"))
            self.assertIsNone(module._optional_timeout_seconds("QUALITY_RULE_LANGFUSE_TIMEOUT_SECONDS"))

    def test_ai_fallback_available_requires_langfuse_config(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "",
                    "langfuse_public_key": "",
                    "langfuse_base_url": "",
                }
            )
            self.assertFalse(module.ai_fallback_available())

            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )
            self.assertTrue(module.ai_fallback_available())
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_keeps_result_when_langfuse_trace_fails(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )

            fake_completion = mock.Mock()
            fake_completion.choices = [mock.Mock(message=mock.Mock(content='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"create_at","dest_check_field":"create_at","src_sql":"select 1","dest_sql":"select 2","reason":"ok"}'))]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            def fake_trace(*args, **kwargs):
                module._LAST_LANGFUSE_TRACE_ERROR = "http_403: forbidden"
                return False

            with mock.patch.object(module, "maybe_trace_langfuse", side_effect=fake_trace):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    result, meta = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {
                            "tbl": "dwd_user_member_log",
                            "dest_tbl": "dwd_user_member_log",
                            "columns": '["create_at"]',
                            "dest_columns": '["create_at"]',
                        },
                        "missing fields",
                        git_roots=[],
                        return_meta=True,
                    )

            self.assertIsNotNone(result)
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["trace_status"], "langfuse_trace_failed")
            self.assertEqual(meta["trace_reason"], "http_403: forbidden")
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_rejects_unverified_source_etl_field(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )

            fake_completion = mock.Mock()
            fake_completion.choices = [
                mock.Mock(
                    message=mock.Mock(
                        content='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"etl_create_time","dest_check_field":"etl_create_time","src_sql":"select 1","dest_sql":"select 2","reason":"guessed"}'
                    )
                )
            ]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            with mock.patch.object(module, "maybe_trace_langfuse", return_value=True):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    result, meta = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {"tbl": "dwd_user_member_log", "dest_tbl": "dwd_user_member_log", "columns": "[]"},
                        "missing fields",
                        git_roots=[],
                        return_meta=True,
                    )

            self.assertIsNone(result)
            self.assertEqual(meta["status"], "ai_output_unverified_source_field")
            self.assertTrue(meta.get("draft_candidate"))
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_allows_different_verified_check_fields(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )

            fake_completion = mock.Mock()
            fake_completion.choices = [
                mock.Mock(
                    message=mock.Mock(
                        content='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"create_at","dest_check_field":"etl_create_time","src_sql":"select 1","dest_sql":"select 2","reason":"guessed"}'
                    )
                )
            ]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            with mock.patch.object(module, "maybe_trace_langfuse", return_value=True):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    result, meta = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {
                            "tbl": "dwd_user_member_log",
                            "dest_tbl": "dwd_user_member_log",
                            "columns": '["create_at"]',
                            "dest_columns": '["fee_finish_at","etl_create_time"]',
                        },
                        "inconsistent fields",
                        git_roots=[],
                        return_meta=True,
                    )

            self.assertIsNotNone(result)
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(result["src_check_field"], "create_at")
            self.assertEqual(result["dest_check_field"], "etl_create_time")
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_rejects_unverified_dest_field(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )

            fake_completion = mock.Mock()
            fake_completion.choices = [
                mock.Mock(
                    message=mock.Mock(
                        content='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"create_at","dest_check_field":"create_at","src_sql":"select 1","dest_sql":"select 2","reason":"guessed"}'
                    )
                )
            ]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            with mock.patch.object(module, "maybe_trace_langfuse", return_value=True):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    result, meta = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {
                            "tbl": "dwd_user_member_log",
                            "dest_tbl": "dwd_user_member_log",
                            "columns": '["create_at"]',
                            "dest_columns": '["fee_finish_at","etl_create_time"]',
                            "dest_ddl_summary": "CREATE TABLE x (`fee_finish_at` datetime, `etl_create_time` datetime)",
                        },
                        "invalid dest field",
                        git_roots=[],
                        return_meta=True,
                    )

            self.assertIsNone(result)
            self.assertEqual(meta["status"], "ai_output_unverified_dest_field")
            self.assertTrue(meta.get("draft_candidate"))
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_uses_http_fallback_when_openai_missing(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )
            with mock.patch.object(
                module,
                "request_openai_compatible_completion",
                return_value='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"create_at","dest_check_field":"create_at","src_sql":"select 1","dest_sql":"select 2","reason":"http fallback"}',
            ):
                with mock.patch.object(module, "maybe_trace_langfuse", return_value=True):
                    with mock.patch.dict(sys.modules, {"openai": None}):
                        result, meta = module.generate_rule_candidate_with_ai(
                            "dwd",
                            {
                                "tbl": "dwd_x",
                                "dest_tbl": "dwd_x",
                                "columns": '["create_at"]',
                                "dest_columns": '["create_at"]',
                            },
                            "missing fields",
                            git_roots=[],
                            return_meta=True,
                        )

            self.assertIsNotNone(result)
            self.assertEqual(result["src_tbl"], "ods_x")
            self.assertEqual(meta["status"], "ok")
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_traces_failed_request(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )
            with mock.patch.object(module, "request_openai_compatible_completion", side_effect=RuntimeError("timeout")):
                with mock.patch.object(module, "maybe_trace_langfuse", return_value=False) as mocked_trace:
                    result, meta = module.generate_rule_candidate_with_ai(
                        "dwd_sec",
                        {"tbl": "dwd_cst_pay_cost_detail", "dest_tbl": "dwd_cst_pay_cost_detail", "columns": '[]'},
                        "missing fields",
                        git_roots=[],
                        return_meta=True,
                    )
            self.assertIsNone(result)
            self.assertEqual(meta["status"], "ai_request_failed")
            mocked_trace.assert_called_once()
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_generate_rule_candidate_with_ai_exports_langfuse_batch_on_trace_failure(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "enabled": True,
                    "api_key": "dashscope-key",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.6-plus",
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )
            fake_completion = mock.Mock()
            fake_completion.choices = [mock.Mock(message=mock.Mock(content='{"src_db":"ods","src_tbl":"ods_x","src_check_field":"create_at","dest_check_field":"create_at","src_sql":"select 1","dest_sql":"select 2","reason":"ok"}'))]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                with mock.patch.object(module, "maybe_trace_langfuse", return_value=False):
                    with mock.patch.dict(module.os.environ, {"QUALITY_RULE_LANGFUSE_EXPORT_PATH": "/tmp/quality-langfuse-batch.json"}, clear=False):
                        result, meta = module.generate_rule_candidate_with_ai(
                            "dwd",
                            {
                                "tbl": "dwd_user_member_log",
                                "dest_tbl": "dwd_user_member_log",
                                "columns": '["create_at"]',
                                "dest_columns": '["create_at"]',
                            },
                            "missing fields",
                            git_roots=[],
                            return_meta=True,
                        )

            self.assertIsNotNone(result)
            self.assertEqual(meta["trace_status"], "langfuse_trace_failed")
            self.assertTrue(meta.get("trace_export_path"))
            self.assertTrue(Path(meta["trace_export_path"]).exists())
        finally:
            exported = Path("/tmp/quality-langfuse-batch.json")
            if exported.exists():
                exported.unlink()
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_maybe_trace_langfuse_uses_http_fallback_when_sdk_missing(self):
        original = dict(module.QUALITY_RULE_AI_CONFIG)
        try:
            module.QUALITY_RULE_AI_CONFIG.update(
                {
                    "langfuse_secret_key": "secret",
                    "langfuse_public_key": "public",
                    "langfuse_base_url": "https://langfuse.kuainiu.io",
                }
            )
            with mock.patch.object(module, "trace_langfuse_via_http", return_value=True) as mocked_http:
                with mock.patch.dict(sys.modules, {"langfuse": None}):
                    ok = module.maybe_trace_langfuse([{"role": "user", "content": "x"}], "resp", {"a": 1})
            self.assertTrue(ok)
            mocked_http.assert_called_once()
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)

    def test_collect_git_context_prefers_existing_git_matches(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            match_file = root / "dwd_cst_pay_cost_detail.sql"
            init_file = root / "init_dwd_cst_pay_cost_detail.sql"
            other_file = root / "other.sql"
            match_file.write_text("select * from ods_repay_cpop_income_item where create_at >= '{begin}'")
            init_file.write_text("init sql with dwd_cst_pay_cost_detail")
            other_file.write_text("select * from something_else")

            snippets = module.collect_git_context(
                "dwd_cst_pay_cost_detail",
                src_tbl="ods_repay_cpop_income_item",
                git_roots=[str(root)],
                preferred_paths=[str(init_file), str(match_file), str(other_file)],
            )

            self.assertEqual(len(snippets), 1)
            self.assertEqual(snippets[0]["path"], str(match_file))
            self.assertIn("create_at", snippets[0]["snippet"])

    def test_collect_git_context_keeps_full_file_by_default(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            match_file = root / "dwd_cst_pay_cost_detail.sql"
            full_text = (
                "insert overwrite dwd_sec.dwd_cst_pay_cost_detail\n"
                "select * from ods.ods_repay_cpop_income_item\n"
                + ("x" * 3000)
                + "\nwhere create_at >= '${begin}'"
            )
            match_file.write_text(full_text)

            snippets = module.collect_git_context(
                "dwd_cst_pay_cost_detail",
                src_tbl="ods_repay_cpop_income_item",
                git_roots=[str(root)],
                preferred_paths=[str(match_file)],
            )

            self.assertEqual(snippets[0]["snippet"], full_text)

    def test_build_ai_messages_normalizes_datetime_values(self):
        messages = module.build_ai_messages(
            "dwd",
            {
                "tbl": "dwd_x",
                "dest_tbl": "dwd_x",
                "columns": [],
            },
            [{"path": "/tmp/job.sql", "snippet": "select 1", "seen_at": datetime(2026, 6, 4, 18, 31, 0)}],
            "missing fields",
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn('"seen_at": "2026-06-04T18:31:00"', messages[1]["content"])

    def test_build_langfuse_ingestion_batch_keeps_only_git_paths_in_messages(self):
        messages = module.build_ai_messages(
            "dwd_sec",
            {
                "db": "dwd_sec",
                "tbl": "dwd_cst_pay_cost_detail",
                "src_db": "ods",
                "src_tbl": "ods_repay_cpop_income_item",
                "columns": ["create_at"],
            },
            [
                {
                    "path": "/tmp/example.sql",
                    "snippet": "select * from ods.ods_repay_cpop_income_item where create_at >= '{begin}'",
                }
            ],
            "src_check_field/dest_check_field 不一致",
        )

        batch = module.build_langfuse_ingestion_batch(
            messages,
            '{"src_sql":"select 1","dest_sql":"select 1"}',
            {"src_sql": "select 1", "dest_sql": "select 1"},
        )

        trace_input = batch["batch"][0]["body"]["input"]
        generation_input = batch["batch"][1]["body"]["input"]
        trace_payload = json.loads(trace_input[1]["content"])
        generation_payload = json.loads(generation_input[1]["content"])

        self.assertEqual(trace_payload["git_context"], [{"path": "/tmp/example.sql"}])
        self.assertEqual(generation_payload["git_context"], [{"path": "/tmp/example.sql"}])
        self.assertEqual(
            sorted(trace_payload.keys()),
            sorted(["task", "database", "dest_db", "dest_tbl", "src_db", "src_tbl", "failure_reason", "validation_feedback", "git_context"]),
        )
