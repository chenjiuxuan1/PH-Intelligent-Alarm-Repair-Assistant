import sys
import types
import unittest
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
                    result = module.generate_rule_candidate_with_ai(
                        "dwd",
                        {"tbl": "dwd_user_member_log", "dest_tbl": "dwd_user_member_log", "columns": "[]"},
                        "missing fields",
                        git_roots=[],
                    )

            self.assertIsNone(result)
        finally:
            module.QUALITY_RULE_AI_CONFIG.clear()
            module.QUALITY_RULE_AI_CONFIG.update(original)
