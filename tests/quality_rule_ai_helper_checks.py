import sys
import types
import unittest
from datetime import datetime
from unittest import mock

from core import quality_rule_ai_helper as module


class QualityRuleAiHelperTests(unittest.TestCase):
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

    def test_generate_rule_candidate_with_ai_returns_none_when_langfuse_trace_fails(self):
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
            fake_completion.choices = [mock.Mock(message=mock.Mock(content='{"src_db":"ods","src_tbl":"ods_x","src_sql":"select 1","dest_sql":"select 2","reason":"ok"}'))]
            fake_client = mock.Mock()
            fake_client.chat.completions.create.return_value = fake_completion

            fake_openai_module = types.ModuleType("openai")
            fake_openai_module.OpenAI = mock.Mock(return_value=fake_client)

            with mock.patch.object(module, "maybe_trace_langfuse", return_value=False):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    result = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {"tbl": "dwd_user_member_log", "dest_tbl": "dwd_user_member_log"},
                        "missing fields",
                        git_roots=[],
                    )

            self.assertIsNone(result)
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

    def test_generate_rule_candidate_with_ai_rejects_inconsistent_check_fields(self):
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
                        {"tbl": "dwd_user_member_log", "dest_tbl": "dwd_user_member_log", "columns": '["create_at"]'},
                        "inconsistent fields",
                        git_roots=[],
                        return_meta=True,
                    )

            self.assertIsNone(result)
            self.assertEqual(meta["status"], "ai_output_inconsistent_fields")
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
                            {"tbl": "dwd_x", "dest_tbl": "dwd_x", "columns": '["create_at"]'},
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

    def test_build_ai_messages_normalizes_datetime_values(self):
        messages = module.build_ai_messages(
            "dwd",
            {
                "tbl": "dwd_x",
                "dest_tbl": "dwd_x",
                "updated_at": datetime(2026, 6, 4, 18, 30, 0),
            },
            [{"path": "/tmp/job.sql", "snippet": "select 1", "seen_at": datetime(2026, 6, 4, 18, 31, 0)}],
            "missing fields",
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn('"updated_at": "2026-06-04T18:30:00"', messages[1]["content"])
        self.assertIn('"seen_at": "2026-06-04T18:31:00"', messages[1]["content"])
