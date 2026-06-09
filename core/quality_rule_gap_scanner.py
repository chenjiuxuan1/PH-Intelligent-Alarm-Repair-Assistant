#!/usr/bin/env python3
"""
Scan wattrel metadata for auto-generated quality rules that are missing today.

This mirrors wattrel's own rule-generation scope and decision tree without
modifying wattrel code. In dry-run mode it reports which rules already exist,
which ones can be auto-generated, and which ones are still blocked by missing
metadata. In apply mode it inserts the generated rules into
`wattrel_quality_setting`.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.db_config import get_db_connection
from config.config import QUALITY_RULE_FORM_CONFIG, QUALITY_RULE_VALIDATION_CONFIG
from core.quality_rule_ai_helper import generate_rule_candidate_with_ai


COUNT_RULE_DATABASES = (
    "ods",
    "dwd",
    "dim",
    "ods_security",
    "dwd_paimon",
    "dim_sec",
    "dwd_sec",
)
EXISTS_RULE_DATABASES = (
    "ads",
    "ads_sec",
)
SUPPORTED_DATABASES = COUNT_RULE_DATABASES + EXISTS_RULE_DATABASES

COUNT_RULE_NAME = "cnt"
EXISTS_RULE_NAME = "if_exists"
COUNT_MSG_TEMPLATE = "{dest_tbl} 数量不一致  期望值 {src_value}  实际值{dest_value}  差值为 {diff}"
EXISTS_MSG_TEMPLATE = "{dest_tbl} 昨日缺失数据"
DEFAULT_GIT_SCAN_ROOTS = ("/data/git",)
CODE_FILE_SUFFIXES = (".sql", ".py", ".scala", ".sh", ".yaml", ".yml", ".json")
CHECK_FIELD_CANDIDATES = (
    "etl_create_time",
    "etl_update_time",
    "created_at",
    "create_at",
    "requested_at",
    "request_at",
    "updated_at",
    "update_at",
    "request_time",
    "create_time",
    "update_time",
    "request_date",
    "create_date",
    "update_date",
)


def resolve_rule_name(database_name):
    return COUNT_RULE_NAME if database_name in COUNT_RULE_DATABASES else EXISTS_RULE_NAME


def parse_json_list(raw_value):
    if raw_value in (None, "", b""):
        return []
    if isinstance(raw_value, list):
        return raw_value
    try:
        return json.loads(raw_value)
    except Exception:
        return []


def parse_git_roots(raw_value=None):
    text = raw_value if raw_value is not None else os.environ.get("QUALITY_GIT_SCAN_ROOTS", "")
    if not text:
        return default_git_scan_roots()
    roots = []
    for item in text.split(","):
        normalized = item.strip()
        if normalized:
            roots.append(normalized)
    return roots


def default_git_scan_roots():
    country = (os.environ.get("QUALITY_RULE_FORM_COUNTRY") or "ph").strip().lower()
    candidates = [
        f"/data/git/starrocks/workflow/{country}",
        "/data/git/starrocks/workflow",
        "/data/git/starrocks.bk/workflow",
        *DEFAULT_GIT_SCAN_ROOTS,
    ]
    seen = set()
    roots = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isdir(candidate):
            roots.append(candidate)
    return roots


def looks_like_time_field(field_name):
    if not field_name:
        return False
    lower_name = field_name.lower()
    if lower_name in CHECK_FIELD_CANDIDATES:
        return True
    return bool(
        re.search(
            r"(time|date|_at)$",
            lower_name,
        )
    )


def iter_git_candidate_files(git_roots, table_names):
    lowered_tables = tuple(str(name).lower() for name in table_names if name)
    file_name_matches = []
    fallback_matches = []
    for root in git_roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in CODE_FILE_SUFFIXES:
                continue
            if ".git" in path.parts:
                continue
            if lowered_tables and any(table in path.name.lower() for table in lowered_tables):
                file_name_matches.append(path)
            else:
                fallback_matches.append(path)
    if file_name_matches:
        yield from file_name_matches
    else:
        yield from fallback_matches


def infer_git_rule_hints(dest_tbl, src_tbl=None, git_roots=None):
    git_roots = list(git_roots or [])
    if not git_roots:
        return {}

    table_names = [dest_tbl]
    if src_tbl:
        table_names.append(src_tbl)

    upstream_candidates = []
    check_field_candidates = []
    scanned_paths = []

    from_pattern = re.compile(r"\b(?:from|join)\s+([a-zA-Z0-9_\.]+)", re.IGNORECASE)
    where_pattern = re.compile(r"\b([a-zA-Z0-9_]+)\s*(?:>=|>|<=|<|=)\s*[\{\$:'\"]", re.IGNORECASE)

    for path in iter_git_candidate_files(git_roots, table_names):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower_text = text.lower()
        if dest_tbl.lower() not in lower_text and (not src_tbl or src_tbl.lower() not in lower_text):
            continue
        scanned_paths.append(str(path))

        for match in from_pattern.findall(text):
            candidate = match.strip("`")
            candidate_table = candidate.rsplit(".", 1)[-1]
            if candidate_table.lower() == dest_tbl.lower():
                continue
            if src_tbl and candidate_table.lower() == src_tbl.lower():
                upstream_candidates.append(candidate)
                continue
            if candidate_table.startswith(("ods_", "dwd_", "dwb_", "dim_", "ads_")):
                upstream_candidates.append(candidate)

        for candidate in CHECK_FIELD_CANDIDATES:
            if re.search(rf"\b{re.escape(candidate)}\b", text, re.IGNORECASE):
                check_field_candidates.append(candidate)
        for match in where_pattern.findall(text):
            if looks_like_time_field(match):
                check_field_candidates.append(match.lower())

    result = {}
    if upstream_candidates:
        result["dep_tbls"] = [upstream_candidates[0]]
    if check_field_candidates:
        for preferred in CHECK_FIELD_CANDIDATES:
            if preferred in check_field_candidates:
                result["check_field"] = preferred
                break
    if scanned_paths:
        result["git_matches"] = scanned_paths[:20]
    return result
    if isinstance(raw_value, list):
        return raw_value
    try:
        return json.loads(raw_value)
    except Exception:
        return []


def determine_create_field(table_info):
    src_table = table_info.get("src_tbl") or ""
    columns = parse_json_list(table_info.get("columns"))
    origin_src_table = table_info.get("origin_src_tbl") or ""

    create_fields = [
        "create_at",
        "created_at",
        f"{src_table}_create_at",
        f"{src_table}_created_at",
        f"{origin_src_table}_create_at",
        f"{origin_src_table}_created_at",
        "create_time",
        "create_date",
    ]
    update_fields = [
        "update_at",
        "updated_at",
        f"{src_table}_update_at",
        f"{src_table}_updated_at",
        f"{origin_src_table}_update_at",
        f"{origin_src_table}_updated_at",
        "update_time",
        "update_date",
    ]

    for field_set in (create_fields, update_fields):
        for field in field_set:
            if field and field in columns:
                return field
    return None


def enrich_etl_table_info(table, ods_table_by_dest, git_roots=None):
    table = deepcopy(table)
    dependent_tables = parse_json_list(table.get("dep_tbls"))
    if not dependent_tables:
        git_hints = infer_git_rule_hints(table.get("tbl", ""), git_roots=git_roots)
        dependent_tables = parse_json_list(git_hints.get("dep_tbls"))
        if not dependent_tables:
            return None, "缺少 dep_tbls 依赖表配置"
        table["git_matches"] = git_hints.get("git_matches", [])

    source = dependent_tables[0]
    if "." in source:
        table["src_tbl"] = source.rsplit(".", 1)[1]
        table["src_db"] = source.rsplit(".", 1)[0]
    else:
        table["src_tbl"] = source
        table["src_db"] = source.split("_")[0]
    table["dest_db"] = table.get("db")

    src_table_info = ods_table_by_dest.get(table["src_tbl"])
    if src_table_info:
        table["origin_check_field"] = src_table_info.get("check_field")
        table["columns"] = src_table_info.get("columns")
        table["origin_src_tbl"] = src_table_info.get("src_tbl")

    return table, None


def infer_source_check_field(table, git_roots=None):
    dest_db = table.get("dest_db")
    if dest_db in ("ods", "ods_security"):
        return determine_create_field(table)

    origin_check_field = table.get("origin_check_field")
    columns = parse_json_list(table.get("columns"))
    if origin_check_field:
        if columns and origin_check_field in columns:
            return origin_check_field
        if not origin_check_field.lower().startswith("etl_"):
            return origin_check_field

    source_create_field = determine_create_field(table)
    if source_create_field:
        return source_create_field

    git_hints = infer_git_rule_hints(
        table.get("dest_tbl") or table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
    )
    git_check_field = git_hints.get("check_field")
    if git_check_field:
        if not git_check_field.lower().startswith("etl_"):
            return git_check_field
        if columns and git_check_field in columns:
            return git_check_field

    increment_field = table.get("increment_field")
    if increment_field:
        # Source-side fallback is only trustworthy when we can verify the field
        # from source metadata. Generic ETL timestamps like etl_create_time on
        # downstream tables should trigger AI fallback instead of being copied
        # blindly to the upstream SQL.
        if columns and increment_field in columns:
            return increment_field
        if not increment_field.lower().startswith("etl_"):
            return increment_field

    return None


def infer_target_check_field(table, git_roots=None):
    existing = table.get("check_field")
    if existing:
        return existing

    dest_db = table.get("dest_db")
    if dest_db in ("ods", "ods_security"):
        return determine_create_field(table)

    if table.get("increment_field"):
        return table.get("increment_field")

    git_hints = infer_git_rule_hints(
        table.get("dest_tbl") or table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
    )
    return git_hints.get("check_field")


def source_field_looks_unreliable_for_count_rule(table, src_check_field):
    if not src_check_field:
        return True
    field = str(src_check_field).lower()
    if not field.startswith("etl_"):
        return False

    src_db = (table.get("src_db") or "").lower()
    dest_db = (table.get("dest_db") or table.get("db") or "").lower()
    src_tbl = (table.get("src_tbl") or "").lower()
    dest_tbl = (table.get("dest_tbl") or table.get("tbl") or "").lower()

    # For cross-table/cross-layer count checks, a generic etl_* field on the
    # source side is too easy to "succeed" with the wrong lineage semantics.
    # We would rather fall back to AI/manual review than keep generating the
    # same obviously suspicious SQL.
    return src_db != dest_db or src_tbl != dest_tbl


def count_rule_fields_are_consistent(src_check_field, dest_check_field):
    if not src_check_field or not dest_check_field:
        return False
    return str(src_check_field).lower() == str(dest_check_field).lower()


def fast_path_count_rule_needs_ai(table, src_check_field, dest_check_field):
    if not src_check_field or not dest_check_field:
        return False, ""

    src_field = str(src_check_field).lower()
    dest_field = str(dest_check_field).lower()
    if src_field == dest_field:
        return False, ""

    src_is_etl = src_field.startswith("etl_")
    dest_is_etl = dest_field.startswith("etl_")
    if not dest_is_etl or src_is_etl:
        return False, ""

    return True, (
        "快速规则命中目标侧 ETL 时间字段，但源侧为业务时间字段，"
        "该口径不满足要求，需继续生成更合理的校验 SQL"
    )


def build_sql_statements(src_db, src_table, target_db, target_table, src_check_field, dest_check_field, table):
    if src_check_field is None and dest_check_field is None:
        src_sql = f"SELECT COUNT(*) as cnt FROM {src_db}.{src_table}"
        dest_sql = f"SELECT COUNT(*) as cnt FROM {target_db}.{target_table}"
        return src_sql, dest_sql

    id_field = None
    if src_check_field and dest_check_field and src_check_field == dest_check_field and "_id" in src_check_field:
        id_field = src_check_field
    elif src_check_field and "_id" in src_check_field and not dest_check_field:
        id_field = src_check_field
    elif dest_check_field and "_id" in dest_check_field and not src_check_field:
        id_field = dest_check_field

    if id_field:
        src_create_field = infer_source_check_field(table)
        dest_create_field = infer_target_check_field(table)
        if not src_create_field or not dest_create_field:
            return None, None
        src_sql = (
            f"SET @min_id = IFNULL((SELECT MIN({id_field}) FROM {target_db}.{target_table} WHERE {dest_create_field} >= '{{begin}}'),0);"
            f"SET @max_id = (SELECT MAX({id_field}) FROM {src_db}.{src_table} WHERE {id_field} >= @min_id AND {src_create_field} < '{{end}}');"
            f"SELECT COUNT(*) AS cnt FROM {src_db}.`{src_table}` WHERE {id_field} >= @min_id AND {id_field} <= @max_id;"
        )
        dest_sql = (
            f"SET @min_id = IFNULL((SELECT MIN({id_field}) FROM {target_db}.{target_table} WHERE {dest_create_field} >= '{{begin}}'),0);"
            f"SET @max_id = (SELECT MAX({id_field}) FROM {target_db}.{target_table} WHERE {dest_create_field} < '{{end}}');"
            f"SELECT COUNT(*) AS cnt FROM {target_db}.`{target_table}` WHERE {id_field} >= @min_id AND {id_field} <= @max_id;"
        )
        return src_sql, dest_sql

    if not src_check_field or not dest_check_field:
        return None, None

    src_sql = (
        f"SELECT COUNT(*) as cnt FROM {src_db}.`{src_table}` "
        f"WHERE {src_check_field} >= '{{begin}}' AND {src_check_field} < '{{end}}'"
    )
    dest_sql = (
        f"SELECT COUNT(*) as cnt FROM {target_db}.{target_table} "
        f"WHERE {dest_check_field} >= '{{begin}}' AND {dest_check_field} < '{{end}}'"
    )
    return src_sql, dest_sql


def normalize_requested_metric_field(value):
    return (value or "").strip()


def build_requested_metric_candidate_with_ai(
    database_name,
    table,
    target_table,
    target_db,
    requested_metric_field,
    git_roots=None,
    cursor=None,
):
    metric_field = normalize_requested_metric_field(requested_metric_field)
    if not metric_field:
        return None

    ai_table = enrich_ai_schema_context(table, cursor=cursor)
    ai_table["requested_metric_field"] = metric_field
    ai_reason = (
        f"确认表指定需要校验的内容字段: {metric_field}。"
        "请优先围绕这个字段生成可比较的单指标校验 SQL，不要回退到默认 count(*) 或 if_exists。"
    )
    ai_candidate, ai_meta = call_ai_candidate(
        database_name,
        ai_table,
        ai_reason,
        git_roots=git_roots,
    )
    if ai_candidate:
        ai_candidate["requested_metric_field"] = metric_field
        return finalize_candidate_with_validation(
            database_name,
            target_table,
            ai_candidate.get("dest_db") or target_db,
            ai_candidate,
            ai_table,
            git_roots=git_roots,
            cursor=cursor,
            base_reason=f"确认表指定字段 {metric_field}，按指定字段生成规则",
            ai_status=ai_meta.get("status", ""),
        )

    return blocked_result_with_ai_draft(
        ai_table,
        target_table,
        target_db,
        ai_meta,
        f"确认表指定字段 {metric_field}，但 AI 未能生成对应校验 SQL",
        fallback={"check_field": ai_table.get("check_field") or ""},
    )


def fetch_rows(cursor, sql, params=None):
    cursor.execute(sql, params or ())
    return cursor.fetchall()


def _escape_identifier(value):
    return str(value or "").replace("`", "``")


def fetch_table_columns(cursor, database_name, table_name):
    if not database_name or not table_name:
        return []
    sql = f"DESCRIBE `{_escape_identifier(database_name)}`.`{_escape_identifier(table_name)}`"
    try:
        rows = fetch_rows(cursor, sql)
    except Exception:
        return []

    columns = []
    for row in rows or []:
        if isinstance(row, dict):
            field_name = (
                row.get("Field")
                or row.get("field")
                or row.get("COLUMN_NAME")
                or row.get("column_name")
                or row.get("col_name")
            )
        else:
            field_name = row[0] if row else None
        if field_name:
            columns.append(str(field_name))
    return columns


def extract_columns_from_ddl_summary(ddl_summary):
    text = str(ddl_summary or "")
    if not text:
        return []
    columns = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("`"):
            continue
        parts = stripped.split("`", 2)
        if len(parts) >= 3 and parts[1]:
            columns.append(parts[1])
    return columns


def fetch_table_ddl_summary(cursor, database_name, table_name):
    if not database_name or not table_name:
        return ""
    sql = f"SHOW CREATE TABLE `{_escape_identifier(database_name)}`.`{_escape_identifier(table_name)}`"
    try:
        rows = fetch_rows(cursor, sql)
    except Exception:
        return ""
    if not rows:
        return ""
    row = rows[0]
    if isinstance(row, dict):
        ddl = row.get("Create Table") or row.get("create_table") or row.get("Create View") or ""
    else:
        ddl = row[1] if len(row) > 1 else ""
    return str(ddl or "")


def schema_summary(columns, ddl_summary, error_message=None):
    parts = []
    if columns:
        parts.append(f"columns={', '.join(columns)}")
    if ddl_summary:
        compact = " ".join(str(ddl_summary).split())
        parts.append(f"ddl={compact[:500]}")
    if error_message:
        parts.append(f"error={error_message}")
    return "; ".join(parts)


def validation_enabled():
    return QUALITY_RULE_VALIDATION_CONFIG.get("enabled", True)


def validation_window_hours():
    hours = QUALITY_RULE_VALIDATION_CONFIG.get("window_hours", 24)
    try:
        hours = int(hours)
    except Exception:
        hours = 24
    return max(hours, 1)


def should_retry_ai_on_mismatch():
    return QUALITY_RULE_VALIDATION_CONFIG.get("retry_with_ai_on_mismatch", True)


def max_ai_retry_count():
    try:
        value = int(QUALITY_RULE_VALIDATION_CONFIG.get("max_ai_retries", 1))
    except Exception:
        value = 1
    return max(value, 0)


def coerce_scalar_row_value(rows):
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        if not row:
            return None
        return next(iter(row.values()))
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    return row


def compute_metric_diff(src_value, dest_value):
    try:
        if src_value is None or dest_value is None:
            return None
        return float(dest_value) - float(src_value)
    except Exception:
        if str(src_value) == str(dest_value):
            return 0
        return None


def split_sql_statements(sql_text, begin, end):
    rendered = render_validation_sql(sql_text or "", begin, end)
    return [item.strip() for item in rendered.split(";") if item.strip()]


def validation_backend():
    return (QUALITY_RULE_VALIDATION_CONFIG.get("backend") or "sr_gateway").strip().lower()


def sr_validation_base_url():
    return (QUALITY_RULE_VALIDATION_CONFIG.get("sr_base_url") or "https://sr-box.kuainiu.io").rstrip("/")


def sr_validation_token():
    return QUALITY_RULE_VALIDATION_CONFIG.get("sr_token") or "fuxi_demo_token"


def sr_validation_access_mode():
    return QUALITY_RULE_VALIDATION_CONFIG.get("sr_access_mode") or "local"


def sr_validation_timeout_sec():
    return int(QUALITY_RULE_VALIDATION_CONFIG.get("sr_timeout_sec") or 60)


def sr_validation_country(candidate=None):
    if isinstance(candidate, dict):
        if candidate.get("country"):
            return str(candidate["country"]).strip().lower()
        if candidate.get("database"):
            pass
    return str(QUALITY_RULE_FORM_CONFIG.get("country", "ph")).strip().lower()


def request_sr_gateway_json(payload, timeout_sec=None):
    timeout = timeout_sec or sr_validation_timeout_sec()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{sr_validation_base_url()}/api/rust/v1/sr-sandboxes/sql-executions",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {sr_validation_token()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SR Gateway HTTP {exc.code}: {error_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"SR Gateway unreachable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"SR Gateway timeout: {exc}") from exc

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SR Gateway returned non-JSON response: {raw[:300]}") from exc

    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(data.get("message") or data.get("error") or "SR Gateway returned success=false")
    return data


def extract_rows_from_gateway_result(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    for key in ("rows", "records", "list", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value

    nested = data.get("data")
    if nested is not None and nested is not data:
        rows = extract_rows_from_gateway_result(nested)
        if rows:
            return rows

    result = data.get("result")
    if result is not None and result is not data:
        rows = extract_rows_from_gateway_result(result)
        if rows:
            return rows

    return []


def execute_metric_sql(cursor, sql_text, begin, end):
    statements = split_sql_statements(sql_text, begin, end)
    if not statements:
        raise ValueError("缺少待执行 SQL")

    final_rows = None
    for statement in statements:
        cursor.execute(statement)
        lowered = statement.lstrip().lower()
        if lowered.startswith(("select ", "with ")):
            final_rows = cursor.fetchall()
    if final_rows is None:
        raise ValueError("SQL 未返回最终结果集")
    return coerce_scalar_row_value(final_rows), statements


def execute_metric_sql_via_sr_gateway(sql_text, begin, end, country):
    statements = split_sql_statements(sql_text, begin, end)
    if not statements:
        raise ValueError("缺少待执行 SQL")

    final_rows = None
    for statement in statements:
        payload = {
            "taskName": "quality-rule-validation",
            "country": country,
            "purpose": "agent",
            "accessMode": sr_validation_access_mode(),
            "sqlMode": "query",
            "sql": statement,
            "page": 1,
            "pageSize": 100,
            "timeoutSec": sr_validation_timeout_sec(),
        }
        response = request_sr_gateway_json(payload, timeout_sec=sr_validation_timeout_sec())
        lowered = statement.lstrip().lower()
        if lowered.startswith(("select ", "with ", "show ", "desc ", "describe ", "explain ")):
            final_rows = extract_rows_from_gateway_result(response)
    if final_rows is None:
        raise ValueError("SQL 未返回最终结果集")
    return coerce_scalar_row_value(final_rows), statements


def execute_metric_sql_with_validation_backend(cursor, sql_text, begin, end, candidate=None):
    if validation_backend() == "db":
        return execute_metric_sql(cursor, sql_text, begin, end)
    return execute_metric_sql_via_sr_gateway(sql_text, begin, end, sr_validation_country(candidate))


def build_syntax_probe_statement(statement):
    lowered = statement.lstrip().lower()
    if lowered.startswith(("select ", "with ")):
        return f"EXPLAIN {statement}"
    return statement


def validate_sql_syntax_with_validation_backend(cursor, sql_text, begin, end, candidate=None, force_db=False):
    statements = split_sql_statements(sql_text, begin, end)
    if not statements:
        raise ValueError("缺少待执行 SQL")

    if force_db or validation_backend() == "db":
        if cursor is None:
            raise ValueError("缺少数据库游标，无法执行直连语法校验")
        for statement in statements:
            cursor.execute(build_syntax_probe_statement(statement))
        return statements

    country = sr_validation_country(candidate)
    for statement in statements:
        payload = {
            "taskName": "quality-rule-syntax-check",
            "country": country,
            "purpose": "agent",
            "accessMode": sr_validation_access_mode(),
            "sqlMode": "query",
            "sql": build_syntax_probe_statement(statement),
            "page": 1,
            "pageSize": 20,
            "timeoutSec": sr_validation_timeout_sec(),
        }
        request_sr_gateway_json(payload, timeout_sec=sr_validation_timeout_sec())
    return statements


def validate_candidate_sql_syntax(cursor, candidate, force_db=False):
    begin, end = build_validation_window()
    try:
        src_statements = validate_sql_syntax_with_validation_backend(
            cursor, candidate.get("src_sql", ""), begin, end, candidate=candidate, force_db=force_db
        )
        dest_statements = validate_sql_syntax_with_validation_backend(
            cursor, candidate.get("dest_sql", ""), begin, end, candidate=candidate, force_db=force_db
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"SQL 语法校验失败: {exc}",
            "validation_status": "syntax_failed",
            "validation_error": str(exc),
            "validation_window_begin": begin,
            "validation_window_end": end,
            "src_statements": [],
            "dest_statements": [],
        }

    return {
        "ok": True,
        "reason": "SQL 语法校验通过",
        "validation_status": "syntax_ok",
        "validation_error": "",
        "validation_window_begin": begin,
        "validation_window_end": end,
        "src_statements": src_statements,
        "dest_statements": dest_statements,
    }


def enrich_ai_schema_context(table, cursor=None):
    table = deepcopy(table)
    own_conn = None
    own_cursor = cursor
    if own_cursor is None:
        try:
            own_conn = get_db_connection()
            own_cursor = own_conn.cursor()
        except Exception:
            own_conn = None
            own_cursor = None

    try:
        raw_source_columns = parse_json_list(table.get("source_columns") or table.get("columns"))
        raw_dest_columns = parse_json_list(table.get("dest_columns"))
        source_schema_error = ""
        dest_schema_error = ""
        if raw_source_columns:
            table["source_columns"] = raw_source_columns
        else:
            table["source_columns"] = []
        if raw_dest_columns:
            table["dest_columns"] = raw_dest_columns
        else:
            table["dest_columns"] = []
        src_db = table.get("src_db")
        src_tbl = table.get("src_tbl")
        dest_db = table.get("dest_db") or table.get("db")
        dest_tbl = table.get("dest_tbl") or table.get("tbl")

        if own_cursor is not None:
            try:
                if not table.get("source_columns") and src_db and src_tbl:
                    table["source_columns"] = fetch_table_columns(own_cursor, src_db, src_tbl)
                if src_db and src_tbl and not table.get("source_ddl_summary"):
                    table["source_ddl_summary"] = fetch_table_ddl_summary(own_cursor, src_db, src_tbl)
                if not table.get("source_columns") and table.get("source_ddl_summary"):
                    table["source_columns"] = extract_columns_from_ddl_summary(table.get("source_ddl_summary"))
            except Exception as exc:
                source_schema_error = str(exc)
            try:
                if dest_db and dest_tbl and not table.get("dest_columns"):
                    table["dest_columns"] = fetch_table_columns(own_cursor, dest_db, dest_tbl)
                if dest_db and dest_tbl and not table.get("dest_ddl_summary"):
                    table["dest_ddl_summary"] = fetch_table_ddl_summary(own_cursor, dest_db, dest_tbl)
                if not table.get("dest_columns") and table.get("dest_ddl_summary"):
                    table["dest_columns"] = extract_columns_from_ddl_summary(table.get("dest_ddl_summary"))
            except Exception as exc:
                dest_schema_error = str(exc)
        table["source_schema_status"] = "ok" if table.get("source_columns") or table.get("source_ddl_summary") else ("error" if source_schema_error else "missing")
        table["dest_schema_status"] = "ok" if table.get("dest_columns") or table.get("dest_ddl_summary") else ("error" if dest_schema_error else "missing")
        table["source_schema_error"] = source_schema_error
        table["dest_schema_error"] = dest_schema_error
        return table
    finally:
        if own_conn is not None:
            own_conn.close()


def load_ods_table_by_dest(cursor):
    rows = fetch_rows(cursor, "SELECT * FROM wattrel_ods_table_settings")
    return {row["dest_tbl"]: row for row in rows if row.get("dest_tbl")}


def load_quality_rules(cursor, dest_db):
    rows = fetch_rows(
        cursor,
        "SELECT * FROM wattrel_quality_setting WHERE dest_db = %s",
        (dest_db,),
    )
    rule_map = {}
    for row in rows:
        rule_map.setdefault(row.get("dest_tbl"), {})[row.get("name")] = row
    return rule_map


def load_tables(cursor, database_name, monitor_level=None):
    if database_name in ("ods", "ods_security"):
        sql = "SELECT * FROM wattrel_ods_table_settings WHERE dest_db = %s AND is_auto_check = 1"
    else:
        sql = "SELECT * FROM wattrel_etl_table_settings WHERE db = %s AND is_auto_check = 1"
    params = [database_name]
    if monitor_level is not None:
        sql += " AND monitor_level = %s"
        params.append(monitor_level)
    return fetch_rows(cursor, sql, tuple(params))


def list_pending_generation_tables(databases=None, monitor_level=None):
    databases = tuple(databases or SUPPORTED_DATABASES)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            items = []
            for database_name in databases:
                tables = load_tables(cursor, database_name, monitor_level=monitor_level)
                rule_map = load_quality_rules(cursor, database_name)
                rule_name = resolve_rule_name(database_name)
                for table in tables:
                    target_table = table["dest_tbl"] if database_name in ("ods", "ods_security") else table["tbl"]
                    if rule_map.get(target_table, {}).get(rule_name):
                        continue
                    if database_name in ("ods", "ods_security"):
                        if table.get("pk") is None:
                            continue
                        if table.get("dest_tbl_partition_field") is not None:
                            continue
                        dest_db = table.get("dest_db") or database_name
                    else:
                        dest_db = table.get("db") or database_name
                    items.append(
                        {
                            "database": database_name,
                            "tbl": target_table,
                            "dest_db": dest_db,
                            "rule_name": rule_name,
                            "status": "pending_generation",
                            "reason": f"缺少 {rule_name} 规则，待进入自动生成",
                            "monitor_level": table.get("monitor_level"),
                        }
                    )
            return items
    finally:
        conn.close()


def build_count_rule_candidate(
    database_name,
    table,
    rule_map,
    ods_table_by_dest,
    git_roots=None,
    cursor=None,
    requested_metric_field=None,
):
    target_table = table["dest_tbl"] if database_name in ("ods", "ods_security") else table["tbl"]
    requested_metric_field = normalize_requested_metric_field(requested_metric_field or table.get("requested_metric_field"))
    existing_rule = rule_map.get(target_table, {}).get(COUNT_RULE_NAME)
    if existing_rule:
        return {
            "status": "existing",
            "rule_name": COUNT_RULE_NAME,
            "dest_tbl": target_table,
            "dest_db": existing_rule.get("dest_db") or table.get("dest_db") or table.get("db"),
            "reason": "已存在 cnt 规则",
            "rule": existing_rule,
        }

    if database_name in ("ods", "ods_security"):
        if table.get("pk") is None:
            return {
                "status": "skipped",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": table.get("dest_db"),
                "reason": "ODS 表缺少主键，wattrel 原逻辑跳过",
            }
        if table.get("dest_tbl_partition_field") is not None:
            return {
                "status": "skipped",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": table.get("dest_db"),
                "reason": "ODS 分区表，wattrel 原逻辑跳过",
            }
        working_table = deepcopy(table)
    else:
        working_table, enrich_error = enrich_etl_table_info(table, ods_table_by_dest, git_roots=git_roots)
        if working_table is None:
            ai_table = enrich_ai_schema_context(
                {
                    **table,
                    "dest_tbl": target_table,
                    "dest_db": table.get("dest_db") or table.get("db"),
                    "requested_metric_field": requested_metric_field,
                },
                cursor=cursor,
            )
            if requested_metric_field:
                ai_result = build_requested_metric_candidate_with_ai(
                    database_name,
                    ai_table,
                    target_table,
                    ai_table.get("dest_db") or table.get("db"),
                    requested_metric_field,
                    git_roots=git_roots,
                    cursor=cursor,
                )
                if ai_result:
                    return ai_result
            ai_candidate, ai_meta = call_ai_candidate(database_name, ai_table, enrich_error, git_roots=git_roots)
            if ai_candidate:
                return finalize_candidate_with_validation(
                    database_name,
                    target_table,
                    ai_candidate.get("dest_db") or table.get("db"),
                    ai_candidate,
                    ai_table,
                    git_roots=git_roots,
                    cursor=cursor,
                    base_reason=f"AI 兜底生成规则: {ai_candidate.get('ai_reason', enrich_error)}",
                    ai_status=ai_meta.get("status", ""),
                )
            return blocked_result_with_ai_draft(
                ai_table,
                target_table,
                table.get("db"),
                ai_meta,
                enrich_error,
            )

    working_table = enrich_ai_schema_context(working_table, cursor=cursor)
    if requested_metric_field:
        custom_result = build_requested_metric_candidate_with_ai(
            database_name,
            working_table,
            target_table,
            working_table.get("dest_db") or working_table.get("db"),
            requested_metric_field,
            git_roots=git_roots,
            cursor=cursor,
        )
        if custom_result:
            return custom_result

    if database_name != "dim":
        src_check_field = infer_source_check_field(working_table, git_roots=git_roots)
        dest_check_field = infer_target_check_field(working_table, git_roots=git_roots)
        if source_field_looks_unreliable_for_count_rule(working_table, src_check_field):
            src_check_field = None
        if not src_check_field or not dest_check_field:
            ai_candidate, ai_meta = call_ai_candidate(
                database_name,
                working_table,
                "无法推断 src_check_field/dest_check_field",
                git_roots=git_roots,
            )
            if ai_candidate:
                return finalize_candidate_with_validation(
                    database_name,
                    target_table,
                    ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                    ai_candidate,
                    working_table,
                    git_roots=git_roots,
                    cursor=cursor,
                    base_reason=f"AI 兜底生成规则: {ai_candidate.get('ai_reason', '无法推断 src_check_field/dest_check_field')}",
                    ai_status=ai_meta.get("status", ""),
                )
            return blocked_result_with_ai_draft(
                working_table,
                target_table,
                working_table.get("dest_db") or working_table.get("db"),
                ai_meta,
                "无法推断 src_check_field/dest_check_field",
                fallback={"check_field": dest_check_field or ""},
            )
        needs_ai, needs_ai_reason = fast_path_count_rule_needs_ai(
            working_table,
            src_check_field,
            dest_check_field,
        )
        if needs_ai:
            ai_candidate, ai_meta = call_ai_candidate(
                database_name,
                working_table,
                needs_ai_reason,
                git_roots=git_roots,
            )
            if ai_candidate:
                return finalize_candidate_with_validation(
                    database_name,
                    target_table,
                    ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                    ai_candidate,
                    working_table,
                    git_roots=git_roots,
                    cursor=cursor,
                    base_reason=f"AI 兜底生成规则: {ai_candidate.get('ai_reason', needs_ai_reason)}",
                    ai_status=ai_meta.get("status", ""),
                )
            return blocked_result_with_ai_draft(
                working_table,
                target_table,
                working_table.get("dest_db") or working_table.get("db"),
                ai_meta,
                needs_ai_reason,
                fallback={"check_field": dest_check_field or ""},
            )
    else:
        src_check_field = None
        dest_check_field = None

    src_db = working_table.get("src_db")
    src_table = working_table.get("src_tbl")
    if src_db is None:
        ai_candidate, ai_meta = call_ai_candidate(
            database_name,
            working_table,
            "无法获取 src_db",
            git_roots=git_roots,
        )
        if ai_candidate:
            return finalize_candidate_with_validation(
                database_name,
                target_table,
                ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                ai_candidate,
                working_table,
                git_roots=git_roots,
                cursor=cursor,
                base_reason=f"AI 兜底生成规则: {ai_candidate.get('ai_reason', '无法获取 src_db')}",
                ai_status=ai_meta.get("status", ""),
            )
        return blocked_result_with_ai_draft(
            working_table,
            target_table,
            working_table.get("dest_db") or working_table.get("db"),
            ai_meta,
            "无法获取 src_db",
        )

    target_db = working_table.get("dest_db") or working_table.get("db")
    src_sql, dest_sql = build_sql_statements(
        src_db,
        src_table,
        target_db,
        target_table,
        src_check_field,
        dest_check_field,
        working_table,
    )
    if not src_sql or not dest_sql:
        ai_candidate, ai_meta = call_ai_candidate(
            database_name,
            working_table,
            "无法构造 src_sql/dest_sql",
            git_roots=git_roots,
        )
        if ai_candidate:
            return finalize_candidate_with_validation(
                database_name,
                target_table,
                ai_candidate.get("dest_db") or target_db,
                ai_candidate,
                working_table,
                git_roots=git_roots,
                cursor=cursor,
                base_reason=f"AI 兜底生成规则: {ai_candidate.get('ai_reason', '无法构造 src_sql/dest_sql')}",
                ai_status=ai_meta.get("status", ""),
            )
        return blocked_result_with_ai_draft(
            working_table,
            target_table,
            target_db,
            ai_meta,
            "无法构造 src_sql/dest_sql",
            fallback={"src_db": src_db, "src_tbl": src_table, "check_field": dest_check_field or ""},
        )

    candidate = {
        "name": COUNT_RULE_NAME,
        "desc": "总数",
        "src_db": src_db,
        "src_tbl": src_table,
        "dest_db": target_db,
        "dest_tbl": target_table,
        "src_sql": src_sql,
        "dest_sql": dest_sql,
        "msg_template": COUNT_MSG_TEMPLATE,
        "check_field": dest_check_field,
        "src_check_field": src_check_field,
        "dest_check_field": dest_check_field,
        "git_matches": working_table.get("git_matches", []),
    }
    return finalize_candidate_with_validation(
        database_name,
        target_table,
        target_db,
        candidate,
        working_table,
        git_roots=git_roots,
        cursor=cursor,
        base_reason="可自动生成规则",
    )


def call_ai_candidate(database_name, table, failure_reason, git_roots=None):
    result = generate_rule_candidate_with_ai(
        database_name,
        table,
        failure_reason,
        git_roots=git_roots,
        return_meta=True,
    )
    if isinstance(result, tuple) and len(result) == 2:
        return result
    if result:
        return result, {"status": "ok", "reason": "", "git_matches": [], "attempted": True}
    return None, {"status": "not_called", "reason": "", "git_matches": [], "attempted": False}


def build_validation_feedback(candidate, validation_result):
    return {
        "previous_src_check_field": candidate.get("src_check_field") or "",
        "previous_dest_check_field": candidate.get("dest_check_field") or "",
        "previous_src_sql": candidate.get("src_sql") or "",
        "previous_dest_sql": candidate.get("dest_sql") or "",
        "validation_status": validation_result.get("validation_status") or "",
        "src_value": validation_result.get("src_value"),
        "dest_value": validation_result.get("dest_value"),
        "diff": validation_result.get("diff"),
        "validation_error": validation_result.get("validation_error") or "",
        "validation_window_begin": validation_result.get("validation_window_begin") or "",
        "validation_window_end": validation_result.get("validation_window_end") or "",
    }


def format_validation_reason(validation_result):
    status = validation_result.get("validation_status") or "unknown"
    if status == "matched":
        return "SQL 可运行且校验结果一致"
    if status == "mismatched":
        return (
            f"SQL 可运行但结果不一致: src_value={validation_result.get('src_value')} "
            f"dest_value={validation_result.get('dest_value')} diff={validation_result.get('diff')}"
        )
    return f"无法完成真实校验: {validation_result.get('validation_error') or validation_result.get('reason') or 'unknown'}"


def wrap_result_with_database(item, database_name, country=None):
    wrapped = {"database": database_name, **item}
    if country:
        wrapped["country"] = country
    return wrapped


def blocked_result_with_candidate(database_name, target_table, target_db, candidate, validation_result, reason, country=None, ai_status=""):
    result = {
        "status": "blocked",
        "rule_name": candidate.get("name", COUNT_RULE_NAME),
        "dest_tbl": target_table,
        "dest_db": target_db,
        "src_db": candidate.get("src_db", ""),
        "src_tbl": candidate.get("src_tbl", ""),
        "src_sql": candidate.get("src_sql", ""),
        "dest_sql": candidate.get("dest_sql", ""),
        "check_field": candidate.get("dest_check_field") or candidate.get("check_field") or "",
        "git_matches": candidate.get("git_matches", []),
        "reason": reason,
        "candidate": candidate,
        "validation_status": validation_result.get("validation_status"),
        "validation_reason": validation_result.get("reason", ""),
        "validation_error": validation_result.get("validation_error", ""),
        "validation_window_begin": validation_result.get("validation_window_begin"),
        "validation_window_end": validation_result.get("validation_window_end"),
        "src_value": validation_result.get("src_value"),
        "dest_value": validation_result.get("dest_value"),
        "diff": validation_result.get("diff"),
        "ai_status": ai_status or validation_result.get("ai_status", ""),
    }
    return wrap_result_with_database(result, database_name, country=country)


def make_validation_result(validation_status, reason, validation_error=""):
    return {
        "ok": False,
        "reason": reason,
        "validation_status": validation_status,
        "validation_error": validation_error,
        "validation_window_begin": "",
        "validation_window_end": "",
        "src_value": None,
        "dest_value": None,
        "diff": None,
    }


def maybe_retry_candidate_with_ai(database_name, working_table, candidate, validation_result, git_roots=None):
    if not should_retry_ai_on_mismatch():
        return None, {"status": "ai_retry_disabled", "reason": "未启用 AI 二次修正"}
    if validation_result.get("validation_status") != "mismatched":
        return None, {"status": "ai_retry_not_needed", "reason": "仅在结果不一致时触发 AI 二次修正"}
    if max_ai_retry_count() <= 0:
        return None, {"status": "ai_retry_disabled", "reason": "AI 二次修正次数为 0"}

    retry_table = enrich_ai_schema_context(
        {
            **working_table,
            "validation_feedback": build_validation_feedback(candidate, validation_result),
        }
    )
    retry_reason = (
        "上一版 SQL 可执行但结果不一致，请根据真实结果、表结构和 Git 片段重新生成更合理的单指标校验 SQL。"
    )
    retry_candidate, retry_meta = call_ai_candidate(
        database_name,
        retry_table,
        retry_reason,
        git_roots=git_roots,
    )
    return retry_candidate, retry_meta


def finalize_candidate_with_validation(database_name, target_table, target_db, candidate, working_table, git_roots=None, cursor=None, country=None, base_reason="可自动生成规则", ai_status=""):
    result = {
        "status": "candidate",
        "rule_name": candidate.get("name", COUNT_RULE_NAME),
        "dest_tbl": target_table,
        "dest_db": target_db,
        "reason": base_reason,
        "candidate": candidate,
        "ai_status": ai_status,
    }
    result = wrap_result_with_database(result, database_name, country=country)
    if not count_rule_fields_are_consistent(
        candidate.get("src_check_field"),
        candidate.get("dest_check_field"),
    ):
        validation_result = make_validation_result(
            "not_validated",
            "基础要求不满足：两个校验语句的限制字段必须一致",
        )
        return blocked_result_with_candidate(
            database_name,
            target_table,
            target_db,
            candidate,
            validation_result,
            f"{base_reason}; 基础要求不满足：src_check_field 和 dest_check_field 必须一致，需人工确认",
            country=country,
            ai_status=ai_status,
        )
    if not validation_enabled():
        result["validation_status"] = "skipped"
        result["validation_reason"] = "未启用真实校验"
        return result

    own_conn = None
    validation_cursor = cursor
    if validation_backend() == "db" and validation_cursor is None:
        own_conn = get_db_connection()
        validation_cursor = own_conn.cursor()
    try:
        validation_result = validate_candidate_sql(validation_cursor, candidate)
    finally:
        if own_conn is not None:
            own_conn.close()
    result.update(
        {
            "validation_status": validation_result.get("validation_status"),
            "validation_reason": validation_result.get("reason", ""),
            "validation_error": validation_result.get("validation_error", ""),
            "validation_window_begin": validation_result.get("validation_window_begin"),
            "validation_window_end": validation_result.get("validation_window_end"),
            "src_value": validation_result.get("src_value"),
            "dest_value": validation_result.get("dest_value"),
            "diff": validation_result.get("diff"),
        }
    )
    if validation_result.get("validation_status") == "matched":
        result["reason"] = f"{base_reason}; {format_validation_reason(validation_result)}"
        return result

    if validation_result.get("validation_status") == "validation_failed":
        return blocked_result_with_candidate(
            database_name,
            target_table,
            target_db,
            candidate,
            validation_result,
            f"{base_reason}; SR 校验失败，需人工验证: {validation_result.get('reason', '')}",
            country=country,
            ai_status=ai_status,
        )

    retry_candidate, retry_meta = maybe_retry_candidate_with_ai(
        database_name,
        working_table,
        candidate,
        validation_result,
        git_roots=git_roots,
    )
    if retry_candidate:
        own_retry_conn = None
        retry_cursor = cursor
        if validation_backend() == "db" and retry_cursor is None:
            own_retry_conn = get_db_connection()
            retry_cursor = own_retry_conn.cursor()
        try:
            retry_validation = validate_candidate_sql(retry_cursor, retry_candidate)
        finally:
            if own_retry_conn is not None:
                own_retry_conn.close()
        if retry_validation.get("validation_status") == "matched":
            retried = {
                "status": "candidate",
                "rule_name": retry_candidate.get("name", COUNT_RULE_NAME),
                "dest_tbl": target_table,
                "dest_db": target_db,
                "reason": f"AI 二次修正后通过真实校验: {retry_candidate.get('ai_reason', retry_validation.get('reason', ''))}",
                "candidate": retry_candidate,
                "ai_status": retry_meta.get("status", ""),
                "validation_status": retry_validation.get("validation_status"),
                "validation_reason": retry_validation.get("reason", ""),
                "validation_error": retry_validation.get("validation_error", ""),
                "validation_window_begin": retry_validation.get("validation_window_begin"),
                "validation_window_end": retry_validation.get("validation_window_end"),
                "src_value": retry_validation.get("src_value"),
                "dest_value": retry_validation.get("dest_value"),
                "diff": retry_validation.get("diff"),
                "ai_retry_count": 1,
            }
            return wrap_result_with_database(retried, database_name, country=country)
        retry_reason = retry_candidate.get("ai_reason") or retry_validation.get("reason") or retry_meta.get("reason", "")
        blocked = blocked_result_with_candidate(
            database_name,
            target_table,
            target_db,
            retry_candidate,
            retry_validation,
            f"SQL 可运行但结果不一致，需人工验证；AI 二次修正后仍未通过: {retry_reason}",
            country=country,
            ai_status=retry_meta.get("status", ""),
        )
        blocked["ai_retry_count"] = 1
        return blocked

    blocked = blocked_result_with_candidate(
        database_name,
        target_table,
        target_db,
        candidate,
        validation_result,
        f"SQL 可运行但结果不一致，需人工验证；AI 二次修正未成功: {retry_meta.get('status', '')} {retry_meta.get('reason', '')}".strip(),
        country=country,
        ai_status=retry_meta.get("status", ""),
    )
    blocked["ai_retry_count"] = 1 if max_ai_retry_count() > 0 else 0
    return blocked


def blocked_result_with_ai_draft(table, target_table, target_db, ai_meta, default_reason, fallback=None, rule_name=COUNT_RULE_NAME):
    draft = ai_meta.get("draft_candidate") or {}
    fallback = fallback or {}
    return {
        "status": "blocked",
        "rule_name": rule_name,
        "dest_tbl": target_table,
        "dest_db": target_db,
        "src_db": draft.get("src_db") or fallback.get("src_db", "") or table.get("src_db", ""),
        "src_tbl": draft.get("src_tbl") or fallback.get("src_tbl", "") or table.get("src_tbl", ""),
        "src_sql": draft.get("src_sql") or fallback.get("src_sql", ""),
        "dest_sql": draft.get("dest_sql") or fallback.get("dest_sql", ""),
        "check_field": draft.get("dest_check_field") or draft.get("check_field") or fallback.get("check_field", ""),
        "git_matches": draft.get("git_matches") or fallback.get("git_matches", []) or table.get("git_matches", []) or ai_meta.get("git_matches", []),
        "reason": f"{default_reason}; AI状态={ai_meta.get('status', 'not_called')} {ai_meta.get('reason', '')}".strip(),
        "ai_status": ai_meta.get("status", ""),
        "validation_status": "not_validated",
    }


def infer_exists_target_check_field(table, git_roots=None):
    existing = table.get("check_field")
    if existing:
        lower_existing = existing.lower()
        if not lower_existing.startswith("etl_"):
            return existing
        columns = parse_json_list(table.get("columns"))
        if columns and existing in columns:
            return existing

    target_create_field = determine_create_field(table)
    if target_create_field:
        return target_create_field

    git_hints = infer_git_rule_hints(
        table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
    )
    git_check_field = git_hints.get("check_field")
    if git_check_field:
        lower_git_check_field = git_check_field.lower()
        if not lower_git_check_field.startswith("etl_"):
            return git_check_field
        columns = parse_json_list(table.get("columns"))
        if columns and git_check_field in columns:
            return git_check_field

    increment_field = table.get("increment_field")
    if increment_field:
        lower_increment_field = increment_field.lower()
        if not lower_increment_field.startswith("etl_"):
            return increment_field
        columns = parse_json_list(table.get("columns"))
        if columns and increment_field in columns:
            return increment_field

    return None


def build_exists_rule_candidate(database_name, table, rule_map, git_roots=None, cursor=None, requested_metric_field=None):
    target_table = table["tbl"]
    requested_metric_field = normalize_requested_metric_field(requested_metric_field or table.get("requested_metric_field"))
    if requested_metric_field:
        return build_requested_metric_candidate_with_ai(
            database_name,
            {
                **table,
                "dest_tbl": target_table,
                "dest_db": table.get("db"),
                "requested_metric_field": requested_metric_field,
            },
            target_table,
            table.get("db"),
            requested_metric_field,
            git_roots=git_roots,
            cursor=cursor,
        )
    existing_rule = rule_map.get(target_table, {}).get(EXISTS_RULE_NAME)
    if existing_rule:
        return {
            "status": "existing",
            "rule_name": EXISTS_RULE_NAME,
            "dest_tbl": target_table,
            "dest_db": existing_rule.get("dest_db") or table.get("db"),
            "reason": "已存在 if_exists 规则",
            "rule": existing_rule,
        }

    target_db = table["db"]
    target_check_field = infer_exists_target_check_field(table, git_roots=git_roots)
    if not target_check_field:
        ai_table = enrich_ai_schema_context(
            {
                **table,
                "dest_tbl": target_table,
                "dest_db": target_db,
            },
            cursor=cursor,
        )
        ai_reason = "无法可靠推断 ADS/ADS_SEC 的时间判定字段，已阻止使用 etl_create_time 兜底，请改走 AI 生成更合理的校验 SQL"
        ai_candidate, ai_meta = call_ai_candidate(
            database_name,
            ai_table,
            ai_reason,
            git_roots=git_roots,
        )
        if ai_candidate:
            return finalize_candidate_with_validation(
                database_name,
                target_table,
                target_db,
                ai_candidate,
                ai_table,
                git_roots=git_roots,
                cursor=cursor,
                base_reason="快捷 if_exists 规则无法安全生成，已切换为 AI 生成",
                ai_status=ai_meta.get("status", ""),
            )
        return blocked_result_with_ai_draft(
            ai_table,
            target_table,
            target_db,
            ai_meta,
            "快捷 if_exists 规则无法安全生成，且 AI 未能补出可用 SQL",
            rule_name=EXISTS_RULE_NAME,
        )

    candidate = {
        "name": EXISTS_RULE_NAME,
        "desc": "是否存在",
        "src_db": "",
        "src_tbl": "",
        "dest_db": target_db,
        "dest_tbl": target_table,
        "src_sql": "",
        "dest_sql": (
            f"select count(*) as if_exists from {target_db}.{target_table} "
            f"where {target_check_field} >= DATE_SUB(CURRENT_DATE,INTERVAL 1 day);"
        ),
        "msg_template": EXISTS_MSG_TEMPLATE,
        "check_field": target_check_field,
    }
    return {
        "status": "candidate",
        "rule_name": EXISTS_RULE_NAME,
        "dest_tbl": target_table,
        "dest_db": target_db,
        "reason": "可自动生成 if_exists 规则",
        "candidate": candidate,
    }


def scan_database_rules(cursor, database_name, monitor_level=None, git_roots=None):
    tables = load_tables(cursor, database_name, monitor_level=monitor_level)
    rule_map = load_quality_rules(cursor, database_name)
    ods_table_by_dest = load_ods_table_by_dest(cursor) if database_name in COUNT_RULE_DATABASES else {}

    results = []
    for table in tables:
        if database_name in COUNT_RULE_DATABASES:
            result = build_count_rule_candidate(
                database_name,
                table,
                rule_map,
                ods_table_by_dest,
                git_roots=git_roots,
                cursor=cursor,
            )
        else:
            result = build_exists_rule_candidate(database_name, table, rule_map, git_roots=git_roots)
        result["database"] = database_name
        results.append(result)
    return results


def scan_quality_rule_gaps(databases=None, monitor_level=None, git_roots=None):
    databases = tuple(databases or SUPPORTED_DATABASES)
    git_roots = parse_git_roots(",".join(git_roots) if isinstance(git_roots, (list, tuple)) else git_roots)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            results = []
            for database_name in databases:
                results.extend(
                    scan_database_rules(
                        cursor,
                        database_name,
                        monitor_level=monitor_level,
                        git_roots=git_roots,
                    )
                )
            return results
    finally:
        conn.close()


def apply_candidates(results):
    candidates = [item["candidate"] for item in results if item.get("status") == "candidate"]
    if not candidates:
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for candidate in candidates:
                cursor.execute(
                    """
                    INSERT INTO wattrel_quality_setting
                    (name, `desc`, src_db, src_tbl, dest_db, dest_tbl, src_sql, dest_sql, msg_template)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        candidate["name"],
                        candidate["desc"],
                        candidate["src_db"],
                        candidate["src_tbl"],
                        candidate["dest_db"],
                        candidate["dest_tbl"],
                        candidate["src_sql"],
                        candidate["dest_sql"],
                        candidate["msg_template"],
                    ),
                )
        conn.commit()
        return len(candidates)
    finally:
        conn.close()


def build_validation_window():
    end = datetime.now().replace(microsecond=0)
    begin = end - timedelta(hours=validation_window_hours())
    return begin.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def render_validation_sql(sql_text, begin, end):
    return sql_text.replace("{begin}", begin).replace("{end}", end)


def validate_candidate_sql(cursor, candidate):
    begin, end = build_validation_window()
    try:
        src_value, src_statements = execute_metric_sql_with_validation_backend(
            cursor, candidate.get("src_sql", ""), begin, end, candidate=candidate
        )
        dest_value, dest_statements = execute_metric_sql_with_validation_backend(
            cursor, candidate.get("dest_sql", ""), begin, end, candidate=candidate
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"SQL 真实校验失败: {exc}",
            "validation_status": "validation_failed",
            "validation_error": str(exc),
            "validation_window_begin": begin,
            "validation_window_end": end,
            "src_value": None,
            "dest_value": None,
            "diff": None,
        }

    diff = compute_metric_diff(src_value, dest_value)
    matched = str(src_value) == str(dest_value)
    return {
        "ok": matched,
        "reason": "SQL 可运行且两边结果一致" if matched else f"SQL 可运行但结果不一致: src_value={src_value} dest_value={dest_value} diff={diff}",
        "validation_status": "matched" if matched else "mismatched",
        "validation_error": "",
        "validation_window_begin": begin,
        "validation_window_end": end,
        "src_value": src_value,
        "dest_value": dest_value,
        "diff": diff,
        "src_statements": src_statements,
        "dest_statements": dest_statements,
    }


def validate_candidates(results):
    candidate_items = [item for item in results if item.get("status") == "candidate"]
    if not candidate_items:
        return {"passed": [], "failed": []}

    if validation_backend() == "db":
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                passed = []
                failed = []
                for item in candidate_items:
                    candidate = item["candidate"]
                    result = validate_candidate_sql(cursor, candidate)
                    wrapped = {**item, "candidate": candidate, **result}
                    if result["ok"]:
                        passed.append(wrapped)
                    else:
                        failed.append(wrapped)
                return {"passed": passed, "failed": failed}
        finally:
            conn.close()

    passed = []
    failed = []
    for item in candidate_items:
        candidate = item["candidate"]
        result = validate_candidate_sql(None, candidate)
        wrapped = {**item, "candidate": candidate, **result}
        if result["ok"]:
            passed.append(wrapped)
        else:
            failed.append(wrapped)
    return {"passed": passed, "failed": failed}


def validate_candidates_for_apply(results):
    candidate_items = [item for item in results if item.get("status") == "candidate"]
    if not candidate_items:
        return {"passed": [], "failed": []}

    # Human-confirmed apply should only check SQL syntax/executability via a
    # direct DB connection. It must not depend on the SR Gateway path, which
    # can be unavailable even when the target metadata DB is writable.
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            passed = []
            failed = []
            for item in candidate_items:
                candidate = item["candidate"]
                result = validate_candidate_sql_syntax(cursor, candidate, force_db=True)
                wrapped = {**item, "candidate": candidate, **result}
                if result["ok"]:
                    passed.append(wrapped)
                else:
                    failed.append(wrapped)
            return {"passed": passed, "failed": failed}
    finally:
        conn.close()


def disable_auto_check_for_items(items):
    if not items:
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            updated = 0
            for item in items:
                database_name = item["database"]
                if database_name in ("ods", "ods_security"):
                    cursor.execute(
                        """
                        UPDATE wattrel_ods_table_settings
                        SET is_auto_check = 0
                        WHERE dest_db = %s AND dest_tbl = %s
                        """,
                        (item["dest_db"], item["dest_tbl"]),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE wattrel_etl_table_settings
                        SET is_auto_check = 0
                        WHERE db = %s AND tbl = %s
                        """,
                        (database_name, item["dest_tbl"]),
                    )
                updated += cursor.rowcount
            conn.commit()
            return updated
    finally:
        conn.close()


def summarize_results(results):
    summary = {"existing": 0, "candidate": 0, "blocked": 0, "skipped": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return summary


def format_results(results):
    summary = summarize_results(results)
    lines = []
    lines.append("质量规则缺口扫描结果")
    lines.append(f"  已存在: {summary['existing']}")
    lines.append(f"  可自动补充: {summary['candidate']}")
    lines.append(f"  无法自动补充: {summary['blocked']}")
    lines.append(f"  按 wattrel 规则跳过: {summary['skipped']}")

    for status, title in (
        ("candidate", "可自动补充"),
        ("blocked", "无法自动补充"),
        ("skipped", "按 wattrel 规则跳过"),
    ):
        group = [item for item in results if item["status"] == status]
        if not group:
            continue
        lines.append("")
        lines.append(f"{title}:")
        for item in group:
            detail = f"{item['database']}.{item['dest_tbl']} [{item['rule_name']}] - {item['reason']}"
            if status == "candidate":
                candidate = item["candidate"]
                detail += f" (src={candidate['src_db']}.{candidate['src_tbl']}, check_field={candidate['check_field']})"
                if candidate.get("git_matches"):
                    detail += f" [git hints: {Path(candidate['git_matches'][0]).name}]"
            lines.append(f"  - {detail}")
    return "\n".join(lines)


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Scan wattrel-style quality-rule gaps without modifying wattrel code.")
    parser.add_argument(
        "--databases",
        nargs="*",
        default=list(SUPPORTED_DATABASES),
        help="Subset of databases to scan. Defaults to the full wattrel auto-generation scope.",
    )
    parser.add_argument(
        "--monitor-level",
        type=int,
        default=None,
        help="Optional monitor_level filter, matching wattrel table scans.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert auto-generatable rules into wattrel_quality_setting.",
    )
    parser.add_argument(
        "--git-roots",
        nargs="*",
        default=None,
        help="Optional code roots to scan for SQL/ETL hints, e.g. /data/git.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of the human-readable summary.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    invalid = [db for db in args.databases if db not in SUPPORTED_DATABASES]
    if invalid:
        print(f"不支持的数据库范围: {', '.join(invalid)}")
        return 1

    results = scan_quality_rule_gaps(
        args.databases,
        monitor_level=args.monitor_level,
        git_roots=args.git_roots,
    )

    if args.apply:
        applied = apply_candidates(results)
        print(f"已写入 {applied} 条候选规则到 wattrel_quality_setting")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=json_default))
    else:
        print(format_results(results))
        if not args.apply:
            candidate_count = summarize_results(results)["candidate"]
            if candidate_count:
                print("")
                print(f"提示: 当前有 {candidate_count} 条规则可自动补充，可加 --apply 写入配置表。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
