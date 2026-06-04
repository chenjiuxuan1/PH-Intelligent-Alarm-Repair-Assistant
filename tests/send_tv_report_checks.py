import importlib.util
import io
import json
import unittest
import urllib.error
from unittest import mock


MODULE_PATH = "/Users/jiangchuanchen/Desktop/PH-Intelligent-Alarm-Repair-Assistant/core/send_tv_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("send_tv_report", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, body):
        self._status_code = status_code
        self._body = body

    def getcode(self):
        return self._status_code

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SendTvReportTests(unittest.TestCase):
    def test_module_uses_shared_tv_config(self):
        fake_config = type(
            "FakeConfigModule",
            (),
            {
                "TV_CONFIG": {
                    "api_url": "https://tv.id.example/api",
                    "bot_id": "bot-id-001",
                    "app_id": "alert-id",
                }
            },
        )

        with mock.patch.dict("sys.modules", {"config.config": fake_config}):
            module = load_module()

        self.assertEqual(module.TV_API_URL, "https://tv.id.example/api")
        self.assertEqual(module.TV_BOT_ID, "bot-id-001")
        self.assertEqual(module.TV_APP_ID, "alert-id")

    def test_send_tv_report_uses_alert_messages_payload(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(202, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_tv_report("报告内容", mentions=["user@example.com"])

        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], module.TV_API_URL)
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            captured["body"],
            {
                "appId": "alert",
                "botId": module.TV_BOT_ID,
                "message": "报告内容",
                "mentions": ["user@example.com"],
            },
        )

    def test_send_tv_report_sends_empty_mentions_list_when_not_provided(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(202, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_tv_report("报告内容")

        self.assertTrue(result["success"])
        self.assertEqual(
            captured["body"],
            {
                "appId": "alert",
                "botId": module.TV_BOT_ID,
                "message": "报告内容",
                "mentions": [],
            },
        )

    def test_send_tv_report_allows_bot_id_override(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(202, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_tv_report("报告内容", bot_id="test-bot-id")

        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["botId"], "test-bot-id")

    def test_send_tv_report_treats_http_200_as_success(self):
        module = load_module()

        with mock.patch.object(
            module.urllib.request,
            "urlopen",
            return_value=FakeResponse(200, '{"accepted":true}'),
        ):
            result = module.send_tv_report("报告内容")

        self.assertTrue(result["success"])
        self.assertEqual(result["status_code"], 200)

    def test_send_tv_report_returns_http_error_body(self):
        module = load_module()
        request = module.urllib.request.Request(module.TV_API_URL, method="POST")
        error = urllib.error.HTTPError(
            module.TV_API_URL,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"missing appId"}'),
        )

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=error):
            result = module.send_tv_report("报告内容")

        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(result["response"], '{"error":"missing appId"}')


if __name__ == "__main__":
    unittest.main()
