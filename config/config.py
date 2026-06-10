#!/usr/bin/env python3
"""
Shared runtime configuration for main-path scripts.

All country-specific runtime values should be provided through environment
variables so cluster migrations do not require business-logic changes.
"""

import json
import os


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_APP_ENV_FILE = os.path.join(REPO_ROOT, ".env.local")


def _load_local_env_file():
    env_file = os.environ.get("APP_ENV_FILE", DEFAULT_APP_ENV_FILE)
    if not env_file or not os.path.exists(env_file):
        return

    with open(env_file, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            os.environ[key] = value


_load_local_env_file()


def _get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _require_env(name, help_text):
    value = _get_env(name)
    if not value:
        raise ValueError(
            f"{name}环境变量未设置！\n"
            f"请执行: export {name}='{help_text}'\n"
            f"或在 ~/.bashrc 中添加: export {name}='{help_text}'"
        )
    return value


def _load_fuyan_workflows():
    raw = _get_env("FUYAN_WORKFLOWS_JSON")
    if raw:
        try:
            workflows = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"FUYAN_WORKFLOWS_JSON 不是合法JSON: {exc}") from exc
        if not isinstance(workflows, list):
            raise ValueError("FUYAN_WORKFLOWS_JSON 必须是数组")
        return workflows

    default_project_code = _get_env("DS_FUYAN_PROJECT_CODE", "158515019231232")
    default_project_name = _get_env("DS_FUYAN_PROJECT_NAME", "国内数仓-质量校验")
    return [
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "每日复验全级别数据(W-1)",
            "name": "每日复验全级别数据(W-1)",
            "workflow_code": "158515019703296",
            "code": "158515019703296",
            "schedule": "每日",
            "level": "all",
        },
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "每小时复验1级表数据(D-1)",
            "name": "每小时复验1级表数据(D-1)",
            "workflow_code": "158515019593728",
            "code": "158515019593728",
            "schedule": "每小时",
            "level": "1",
        },
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "每小时复验2级表数据(D-1)",
            "name": "每小时复验2级表数据(D-1)",
            "workflow_code": "158515019630592",
            "code": "158515019630592",
            "schedule": "每小时",
            "level": "2",
        },
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "两小时复验3级表数据(D-1)",
            "name": "两小时复验3级表数据(D-1)",
            "workflow_code": "158515019667456",
            "code": "158515019667456",
            "schedule": "每2小时",
            "level": "3",
        },
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "每周复验全级别数据(M-3)",
            "name": "每周复验全级别数据(M-3)",
            "workflow_code": "158515019741184",
            "code": "158515019741184",
            "schedule": "每周",
            "level": "all",
        },
        {
            "project_name": default_project_name,
            "project_code": default_project_code,
            "workflow_name": "每月11日复验全级别数据(Y-2)",
            "name": "每月11日复验全级别数据(Y-2)",
            "workflow_code": "158515019778048",
            "code": "158515019778048",
            "schedule": "每月",
            "level": "all",
        },
    ]


WORKSPACE_CONFIG = {
    "root": _get_env("APP_WORKSPACE", REPO_ROOT),
}
WORKSPACE_CONFIG["auto_repair_records_dir"] = os.path.join(
    WORKSPACE_CONFIG["root"], "auto_repair_records"
)
WORKSPACE_CONFIG["manual_review_state_file"] = os.path.join(
    WORKSPACE_CONFIG["auto_repair_records_dir"], "manual_review_state.json"
)
WORKSPACE_CONFIG["repair_counts_file"] = os.path.join(
    WORKSPACE_CONFIG["auto_repair_records_dir"], "repair_counts.json"
)
WORKSPACE_CONFIG["quality_rule_backlog_file"] = os.path.join(
    WORKSPACE_CONFIG["auto_repair_records_dir"], "quality_rule_backlog.json"
)
WORKSPACE_CONFIG["quality_rule_sync_state_file"] = os.path.join(
    WORKSPACE_CONFIG["auto_repair_records_dir"], "quality_rule_sync_state.json"
)
WORKSPACE_CONFIG["schedule_export_csv"] = _get_env(
    "SCHEDULE_EXPORT_CSV",
    os.path.join(WORKSPACE_CONFIG["root"], "dolphinscheduler", "schedules_export.csv"),
)


DS_CONFIG = {
    "base_url": _get_env("DS_BASE_URL", "http://172.20.0.235:12345/dolphinscheduler"),
    "token": _get_env("DS_TOKEN", ""),
    "project_code": _get_env("DS_PROJECT_CODE", "158514956085248"),
    "fuyan_project_code": _get_env("DS_FUYAN_PROJECT_CODE", "158515019231232"),
    "environment_code": _get_env("DS_ENVIRONMENT_CODE", "154818922491872"),
    "tenant_code": _get_env("DS_TENANT_CODE", "dolphinscheduler"),
    "fuyan_project_name": _get_env("DS_FUYAN_PROJECT_NAME", "国内数仓-质量校验"),
    "api_mode": _get_env("DS_API_MODE", "process_v2"),
    "start_endpoint": _get_env("DS_START_ENDPOINT", "start-process-instance"),
    "start_code_field": _get_env("DS_START_CODE_FIELD", "processDefinitionCode"),
    "definition_endpoint_style": _get_env("DS_DEFINITION_ENDPOINT_STYLE", "auto"),
    "instance_endpoint_style": _get_env("DS_INSTANCE_ENDPOINT_STYLE", "auto"),
}


TV_CONFIG = {
    "api_url": _get_env("TV_API_URL", "https://tv-service-alert.kuainiu.chat/alert/v2/array"),
    "bot_id": _get_env("TV_BOT_ID", "fbbcabb4-d187-4d9e-8e1e-ba7654a24d1c"),
    "app_id": _get_env("TV_APP_ID", "alert"),
}


DB_CONFIG = {
    "host": _get_env("DB_HOST", "172.20.0.235"),
    "port": int(_get_env("DB_PORT", "13306")),
    "user": _get_env("DB_USER", "e_ds"),
    "password": _get_env("DB_PASSWORD", ""),
    "database": _get_env("DB_NAME", "wattrel"),
    "charset": "utf8mb4",
}


OPENCLAW_CONFIG = {
    "webhook": _get_env("OPENCLAW_WEBHOOK", "http://127.0.0.1:18789/hooks/wattrel/wake"),
    "token": _get_env("OPENCLAW_HOOK_TOKEN", "wattrel-webhook-secret-token-2026"),
}


TABLE_CONFIG = {
    "quality_result_table": _get_env("QUALITY_RESULT_TABLE", "wattrel_quality_result"),
    "quality_alert_table": _get_env("QUALITY_ALERT_TABLE", "wattrel_quality_alert"),
}


# AI fallback defaults for quality rule generation.
# Fill these in directly if you prefer code-based configuration instead of
# exporting shell environment variables. Environment variables still override
# these values when present.
QUALITY_RULE_AI_DEFAULTS = {
    "api_key": "",
    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "model": "qwen3.6-plus",
    "langfuse_secret_key": "sk-lf-fbde8223-da9e-4869-9d88-ba919e45f604",
    "langfuse_public_key": "pk-lf-586c391a-f4f9-4356-92f5-97100828e72c",
    "langfuse_base_url": "https://langfuse.kuainiu.io",
}


QUALITY_RULE_FORM_CONFIG = {
    "country": _get_env("QUALITY_RULE_FORM_COUNTRY", "ph"),
    "view_url": _get_env(
        "QUALITY_RULE_FORM_VIEW_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLScRS0T5w9B0BOmXl88uhVDeLOjfrMbpS3KWFNgG_nLK9SwV5w/viewform?usp=publish-editor",
    ),
    "post_url": _get_env(
        "QUALITY_RULE_FORM_POST_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLScRS0T5w9B0BOmXl88uhVDeLOjfrMbpS3KWFNgG_nLK9SwV5w/formResponse",
    ),
    "field_map": json.loads(
        _get_env(
            "QUALITY_RULE_FORM_FIELD_MAP_JSON",
            json.dumps(
                {
                    "country": "entry.531558451",
                    "database": "entry.1835227505",
                    "tbl": "entry.1870533704",
                    "need_apply": "entry.52956991",
                    "candidate_key": "entry.1641182397",
                    "src_sql": "entry.625807972",
                    "dest_sql": "entry.817070984",
                    "human_check": "entry.943241897",
                },
                ensure_ascii=False,
            ),
        )
    ),
    "required_fields": json.loads(
        _get_env(
            "QUALITY_RULE_FORM_REQUIRED_FIELDS_JSON",
            '["candidate_key", "country", "database", "tbl", "need_apply"]',
        )
    ),
    "confirmation_export_url": _get_env(
        "QUALITY_RULE_CONFIRMATION_EXPORT_URL",
        "https://docs.google.com/spreadsheets/d/1nzjXSrMqg0_sXd2V7JpBWl2xZyNrQC25X8WK50mNP9g/export?format=csv&gid=683783947",
    ),
    "confirmation_sheet_url": _get_env(
        "QUALITY_RULE_CONFIRMATION_SHEET_URL",
        "https://docs.google.com/spreadsheets/d/1nzjXSrMqg0_sXd2V7JpBWl2xZyNrQC25X8WK50mNP9g/edit?resourcekey=&gid=683783947#gid=683783947",
    ),
    "confirmation_spreadsheet_id": _get_env(
        "QUALITY_RULE_CONFIRMATION_SPREADSHEET_ID",
        "1nzjXSrMqg0_sXd2V7JpBWl2xZyNrQC25X8WK50mNP9g",
    ),
    "confirmation_sheet_gid": _get_env(
        "QUALITY_RULE_CONFIRMATION_SHEET_GID",
        "683783947",
    ),
    "confirmation_google_service_account_json": _get_env(
        "QUALITY_RULE_CONFIRMATION_GOOGLE_SERVICE_ACCOUNT_JSON",
        "",
    ),
    "confirmation_google_service_account_file": _get_env(
        "QUALITY_RULE_CONFIRMATION_GOOGLE_SERVICE_ACCOUNT_FILE",
        "",
    ),
    "confirmation_column_map": json.loads(
        _get_env(
            "QUALITY_RULE_CONFIRMATION_COLUMN_MAP_JSON",
            json.dumps(
                {
                    "country": "国家",
                    "database": "数据库",
                    "tbl": "表名",
                    "auto_generate": "是否需要自动生成",
                    "need_apply": "是否上线",
                    "metric_field": "需要校验的内容字段",
                    "candidate_key": "唯一键",
                    "src_sql": "src_sql",
                    "dest_sql": "dest_sql",
                    "human_check": "human_check",
                    "operator": "operator",
                    "notes": "notes",
                    "submitted_at": "时间",
                },
                ensure_ascii=False,
            ),
        )
    ),
    "notify_bot_id": _get_env("QUALITY_RULE_NOTIFY_BOT_ID", "08826b39-e6eb-44fb-9c25-9778a8171f49"),
    "notify_mentions": [
        item.strip()
        for item in _get_env("QUALITY_RULE_NOTIFY_MENTIONS", "").split(",")
        if item.strip()
    ],
    "git_scan_roots": [
        item.strip()
        for item in _get_env("QUALITY_GIT_SCAN_ROOTS", "").split(",")
        if item.strip()
    ],
}


QUALITY_RULE_AI_CONFIG = {
    "enabled": _get_env("QUALITY_RULE_AI_ENABLED", "1") == "1",
    "api_key": _get_env("DASHSCOPE_API_KEY", QUALITY_RULE_AI_DEFAULTS["api_key"]),
    "base_url": _get_env("QUALITY_RULE_AI_BASE_URL", QUALITY_RULE_AI_DEFAULTS["base_url"]),
    "model": _get_env("QUALITY_RULE_AI_MODEL", QUALITY_RULE_AI_DEFAULTS["model"]),
    "langfuse_secret_key": _get_env(
        "LANGFUSE_SECRET_KEY", QUALITY_RULE_AI_DEFAULTS["langfuse_secret_key"]
    ),
    "langfuse_public_key": _get_env(
        "LANGFUSE_PUBLIC_KEY", QUALITY_RULE_AI_DEFAULTS["langfuse_public_key"]
    ),
    "langfuse_base_url": _get_env(
        "LANGFUSE_BASE_URL", QUALITY_RULE_AI_DEFAULTS["langfuse_base_url"]
    ),
}


QUALITY_RULE_VALIDATION_CONFIG = {
    "enabled": _get_env("QUALITY_RULE_VALIDATION_ENABLED", "1") == "1",
    "retry_with_ai_on_mismatch": _get_env("QUALITY_RULE_VALIDATION_AI_RETRY_ENABLED", "1") == "1",
    "max_ai_retries": int(_get_env("QUALITY_RULE_VALIDATION_MAX_AI_RETRIES", "1")),
    "window_hours": int(_get_env("QUALITY_RULE_VALIDATION_WINDOW_HOURS", "24")),
    "backend": _get_env("QUALITY_RULE_VALIDATION_BACKEND", "sr_gateway"),
    "sr_base_url": _get_env("QUALITY_RULE_SR_BASE_URL", _get_env("FUXI_BASE_URL", "https://sr-box.kuainiu.io")),
    "sr_token": _get_env(
        "QUALITY_RULE_SR_TOKEN",
        _get_env("FUXI_API_TOKEN", "fuxi_backend_query_all_20260518"),
    ),
    "sr_access_mode": _get_env("QUALITY_RULE_SR_ACCESS_MODE", "local"),
    "sr_timeout_sec": int(_get_env("QUALITY_RULE_SR_TIMEOUT_SEC", "60")),
}


REPAIR_CONFIG = {
    "scan_lookback_days": int(_get_env("SCAN_LOOKBACK_DAYS", "8")),
    "priority_workflow_codes": json.loads(_get_env("PRIORITY_WORKFLOW_CODES_JSON", "[]")),
    "blocked_workflow_names": json.loads(
        _get_env(
            "BLOCKED_WORKFLOW_NAMES_JSON",
            '["印尼-宽表全量工作流（1D）", "DWS（1D）"]',
        )
    ),
    "blocked_fuyan_workflow_names": json.loads(
        _get_env(
            "BLOCKED_FUYAN_WORKFLOW_NAMES_JSON",
            "[]",
        )
    ),
}


FUYAN_WORKFLOWS = _load_fuyan_workflows()


def get_ds_token():
    """Return the DS token or raise with remediation steps."""
    return _require_env("DS_TOKEN", "your_token_here")


def check_token_set():
    """Return whether the DS token is configured."""
    return bool(_get_env("DS_TOKEN"))


def get_db_config():
    """Return DB config after validating secret presence."""
    if not DB_CONFIG["password"]:
        raise ValueError(
            "DB_PASSWORD环境变量未设置！\n"
            "请执行: export DB_PASSWORD='your_db_password'\n"
            "或在 ~/.bashrc 中添加: export DB_PASSWORD='your_db_password'"
        )
    return DB_CONFIG
