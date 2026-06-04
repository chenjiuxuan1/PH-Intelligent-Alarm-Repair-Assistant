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
from datetime import date, datetime
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.db_config import get_db_connection
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


def fetch_rows(cursor, sql, params=None):
    cursor.execute(sql, params or ())
    return cursor.fetchall()


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


def build_count_rule_candidate(database_name, table, rule_map, ods_table_by_dest, git_roots=None):
    target_table = table["dest_tbl"] if database_name in ("ods", "ods_security") else table["tbl"]
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
            ai_candidate = generate_rule_candidate_with_ai(
                database_name,
                {
                    **table,
                    "dest_tbl": target_table,
                },
                enrich_error,
                git_roots=git_roots,
            )
            if ai_candidate:
                return {
                    "status": "candidate",
                    "rule_name": COUNT_RULE_NAME,
                    "dest_tbl": target_table,
                    "dest_db": ai_candidate.get("dest_db") or table.get("db"),
                    "reason": f"AI 兜底生成 cnt 规则: {ai_candidate.get('ai_reason', enrich_error)}",
                    "candidate": ai_candidate,
                }
            return {
                "status": "blocked",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": table.get("db"),
                "src_db": table.get("src_db", ""),
                "src_tbl": table.get("src_tbl", ""),
                "src_sql": "",
                "dest_sql": "",
                "check_field": "",
                "git_matches": table.get("git_matches", []),
                "reason": enrich_error,
            }

    if database_name != "dim":
        src_check_field = infer_source_check_field(working_table, git_roots=git_roots)
        dest_check_field = infer_target_check_field(working_table, git_roots=git_roots)
        if source_field_looks_unreliable_for_count_rule(working_table, src_check_field):
            src_check_field = None
        if src_check_field and dest_check_field and not count_rule_fields_are_consistent(src_check_field, dest_check_field):
            ai_candidate = generate_rule_candidate_with_ai(
                database_name,
                working_table,
                f"src_check_field/dest_check_field 不一致: {src_check_field} != {dest_check_field}",
                git_roots=git_roots,
            )
            if ai_candidate:
                return {
                    "status": "candidate",
                    "rule_name": COUNT_RULE_NAME,
                    "dest_tbl": target_table,
                    "dest_db": ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                    "reason": f"AI 兜底生成 cnt 规则: {ai_candidate.get('ai_reason', 'src_check_field/dest_check_field 不一致')}",
                    "candidate": ai_candidate,
                }
            return {
                "status": "blocked",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": working_table.get("dest_db") or working_table.get("db"),
                "src_db": working_table.get("src_db", ""),
                "src_tbl": working_table.get("src_tbl", ""),
                "src_sql": "",
                "dest_sql": "",
                "check_field": dest_check_field or "",
                "git_matches": working_table.get("git_matches", []),
                "reason": f"src_check_field/dest_check_field 不一致: {src_check_field} != {dest_check_field}",
            }
        if not src_check_field or not dest_check_field:
            ai_candidate = generate_rule_candidate_with_ai(
                database_name,
                working_table,
                "无法推断 src_check_field/dest_check_field",
                git_roots=git_roots,
            )
            if ai_candidate:
                return {
                    "status": "candidate",
                    "rule_name": COUNT_RULE_NAME,
                    "dest_tbl": target_table,
                    "dest_db": ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                    "reason": f"AI 兜底生成 cnt 规则: {ai_candidate.get('ai_reason', '无法推断 src_check_field/dest_check_field')}",
                    "candidate": ai_candidate,
                }
            return {
                "status": "blocked",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": working_table.get("dest_db") or working_table.get("db"),
                "src_db": working_table.get("src_db", ""),
                "src_tbl": working_table.get("src_tbl", ""),
                "src_sql": "",
                "dest_sql": "",
                "check_field": dest_check_field or "",
                "git_matches": working_table.get("git_matches", []),
                "reason": "无法推断 src_check_field/dest_check_field",
            }
    else:
        src_check_field = None
        dest_check_field = None

    src_db = working_table.get("src_db")
    src_table = working_table.get("src_tbl")
    if src_db is None:
        ai_candidate = generate_rule_candidate_with_ai(
            database_name,
            working_table,
            "无法获取 src_db",
            git_roots=git_roots,
        )
        if ai_candidate:
            return {
                "status": "candidate",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": ai_candidate.get("dest_db") or working_table.get("dest_db") or working_table.get("db"),
                "reason": f"AI 兜底生成 cnt 规则: {ai_candidate.get('ai_reason', '无法获取 src_db')}",
                "candidate": ai_candidate,
            }
        return {
            "status": "blocked",
            "rule_name": COUNT_RULE_NAME,
            "dest_tbl": target_table,
            "dest_db": working_table.get("dest_db") or working_table.get("db"),
            "src_db": working_table.get("src_db", ""),
            "src_tbl": working_table.get("src_tbl", ""),
            "src_sql": "",
            "dest_sql": "",
            "check_field": "",
            "git_matches": working_table.get("git_matches", []),
            "reason": "无法获取 src_db",
        }

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
        ai_candidate = generate_rule_candidate_with_ai(
            database_name,
            working_table,
            "无法构造 src_sql/dest_sql",
            git_roots=git_roots,
        )
        if ai_candidate:
            return {
                "status": "candidate",
                "rule_name": COUNT_RULE_NAME,
                "dest_tbl": target_table,
                "dest_db": ai_candidate.get("dest_db") or target_db,
                "reason": f"AI 兜底生成 cnt 规则: {ai_candidate.get('ai_reason', '无法构造 src_sql/dest_sql')}",
                "candidate": ai_candidate,
            }
        return {
            "status": "blocked",
            "rule_name": COUNT_RULE_NAME,
            "dest_tbl": target_table,
            "dest_db": target_db,
            "src_db": src_db,
            "src_tbl": src_table,
            "src_sql": "",
            "dest_sql": "",
            "check_field": dest_check_field or "",
            "git_matches": working_table.get("git_matches", []),
            "reason": "无法构造 src_sql/dest_sql",
        }

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
    return {
        "status": "candidate",
        "rule_name": COUNT_RULE_NAME,
        "dest_tbl": target_table,
        "dest_db": target_db,
        "reason": "可自动生成 cnt 规则",
        "candidate": candidate,
    }


def build_exists_rule_candidate(database_name, table, rule_map):
    target_table = table["tbl"]
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
            f"where etl_create_time >= DATE_SUB(CURRENT_DATE,INTERVAL 1 day);"
        ),
        "msg_template": EXISTS_MSG_TEMPLATE,
        "check_field": None,
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
            )
        else:
            result = build_exists_rule_candidate(database_name, table, rule_map)
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
    from datetime import datetime, timedelta

    end = datetime.now().replace(minute=0, second=0, microsecond=0)
    begin = end - timedelta(days=1)
    return begin.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def render_validation_sql(sql_text, begin, end):
    return sql_text.replace("{begin}", begin).replace("{end}", end)


def validate_candidate_sql(cursor, candidate):
    begin, end = build_validation_window()
    statements = [
        item.strip()
        for item in render_validation_sql(candidate.get("src_sql", ""), begin, end).split(";")
        if item.strip()
    ]
    statements.extend(
        item.strip()
        for item in render_validation_sql(candidate.get("dest_sql", ""), begin, end).split(";")
        if item.strip()
    )
    if not statements:
        return {"ok": False, "reason": "缺少待验证 SQL"}

    for statement in statements:
        lowered = statement.lower()
        try:
            if lowered.startswith("set "):
                cursor.execute(statement)
            elif lowered.startswith("select "):
                cursor.execute(f"EXPLAIN {statement}")
                cursor.fetchall()
            else:
                cursor.execute(statement)
        except Exception as exc:
            return {"ok": False, "reason": f"SQL 验证失败: {exc}"}
    return {"ok": True, "reason": "SQL 校验通过"}


def validate_candidates(results):
    candidate_items = [item for item in results if item.get("status") == "candidate"]
    if not candidate_items:
        return {"passed": [], "failed": []}

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
