#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from config.config import QUALITY_RULE_AI_CONFIG


CODE_FILE_SUFFIXES = (".sql", ".py", ".scala", ".sh", ".yaml", ".yml", ".json")


def default_git_scan_roots():
    country = (os.environ.get("QUALITY_RULE_FORM_COUNTRY") or "ph").strip().lower()
    candidates = [
        f"/data/git/starrocks/workflow/{country}",
        "/data/git/starrocks/workflow",
        "/data/git/starrocks.bk/workflow",
        "/data/git",
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


def ai_fallback_available():
    return bool(
        QUALITY_RULE_AI_CONFIG.get("enabled")
        and QUALITY_RULE_AI_CONFIG.get("api_key")
        and QUALITY_RULE_AI_CONFIG.get("base_url")
        and QUALITY_RULE_AI_CONFIG.get("model")
        and QUALITY_RULE_AI_CONFIG.get("langfuse_secret_key")
        and QUALITY_RULE_AI_CONFIG.get("langfuse_public_key")
        and QUALITY_RULE_AI_CONFIG.get("langfuse_base_url")
    )


def iter_git_candidate_files(git_roots, table_names):
    lowered_tables = tuple(str(name).lower() for name in table_names if name)
    file_name_matches = []
    fallback_matches = []
    roots = list(git_roots or []) or default_git_scan_roots()
    for root in roots:
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


def collect_git_context(dest_tbl, src_tbl=None, git_roots=None, limit=3):
    table_names = [dest_tbl]
    if src_tbl:
        table_names.append(src_tbl)

    snippets = []
    for path in iter_git_candidate_files(git_roots, table_names):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower_text = text.lower()
        if dest_tbl.lower() not in lower_text and (not src_tbl or src_tbl.lower() not in lower_text):
            continue
        snippet = text[:4000]
        snippets.append({"path": str(path), "snippet": snippet})
        if len(snippets) >= limit:
            break
    return snippets


def build_ai_messages(database_name, table, git_context, failure_reason):
    system_prompt = (
        "你是一个数仓数据质量规则生成助手。"
        "请根据表信息、Git代码片段和失败原因，生成用于数据质量校验的候选规则。"
        "输出必须是严格 JSON，不要输出 Markdown 代码块，不要输出解释。"
    )
    payload = {
        "database": database_name,
        "table": table,
        "failure_reason": failure_reason,
        "git_context": git_context,
        "output_schema": {
            "src_db": "string",
            "src_tbl": "string",
            "src_check_field": "string",
            "dest_check_field": "string",
            "src_sql": "string",
            "dest_sql": "string",
            "reason": "string",
        },
        "constraints": [
            "只生成能用于质量校验的 SQL。",
            "如果是数量校验，请生成源表与目标表的 count SQL。",
            "如果目标表和源表的时间字段不同，必须分别给出 src_check_field 和 dest_check_field。",
            "如果源表时间字段没有在 Git 代码片段或已知元数据中明确出现，不要猜测 etl_create_time/etl_update_time 作为源表字段。",
            "SQL 中如果使用时间窗口，必须保留 {begin} 和 {end} 占位符。",
            "如果无法可靠判断，也要返回最合理的候选，并在 reason 中说明依据。",
        ],
    }
    user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def extract_json_object(text):
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty ai response")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def maybe_trace_langfuse(messages, response_text, parsed_output):
    secret_key = QUALITY_RULE_AI_CONFIG.get("langfuse_secret_key")
    public_key = QUALITY_RULE_AI_CONFIG.get("langfuse_public_key")
    host = QUALITY_RULE_AI_CONFIG.get("langfuse_base_url")
    if not secret_key or not public_key or not host:
        return False
    try:
        from langfuse import Langfuse
    except Exception:
        return False
    try:
        client = Langfuse(
            secret_key=secret_key,
            public_key=public_key,
            host=host,
        )
        trace = client.trace(name="quality_rule_ai_fallback")
        generation = trace.generation(
            name="generate_quality_rule_candidate",
            model=QUALITY_RULE_AI_CONFIG.get("model"),
            input=messages,
        )
        generation.end(output=response_text)
        trace.update(output=parsed_output)
        client.flush()
        return True
    except Exception:
        return False


def source_field_is_verified(field_name, table, git_context):
    if not field_name:
        return False
    normalized = str(field_name).lower()
    if not normalized.startswith("etl_"):
        return True

    raw_columns = table.get("columns")
    columns = []
    if isinstance(raw_columns, list):
        columns = [str(item).lower() for item in raw_columns]
    elif isinstance(raw_columns, str) and raw_columns:
        try:
            parsed_columns = json.loads(raw_columns)
            if isinstance(parsed_columns, list):
                columns = [str(item).lower() for item in parsed_columns]
        except Exception:
            columns = []
    if normalized in columns:
        return True

    for item in git_context or []:
        snippet = (item or {}).get("snippet", "")
        if re.search(rf"\b{re.escape(normalized)}\b", snippet, re.IGNORECASE):
            return True
    return False


def count_rule_fields_are_consistent(parsed_output):
    src_check_field = (parsed_output.get("src_check_field") or "").strip()
    dest_check_field = (parsed_output.get("dest_check_field") or "").strip()
    if not src_check_field or not dest_check_field:
        return False
    return src_check_field.lower() == dest_check_field.lower()


def generate_rule_candidate_with_ai(database_name, table, failure_reason, git_roots=None):
    if not ai_fallback_available():
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    git_context = collect_git_context(
        table.get("dest_tbl") or table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
    )
    messages = build_ai_messages(database_name, table, git_context, failure_reason)
    client = OpenAI(
        api_key=QUALITY_RULE_AI_CONFIG.get("api_key"),
        base_url=QUALITY_RULE_AI_CONFIG.get("base_url"),
    )
    completion = client.chat.completions.create(
        model=QUALITY_RULE_AI_CONFIG.get("model"),
        messages=messages,
    )
    response_text = completion.choices[0].message.content if completion.choices else ""
    parsed = extract_json_object(response_text)
    traced = maybe_trace_langfuse(messages, response_text, parsed)
    if not traced:
        return None

    if not source_field_is_verified(parsed.get("src_check_field"), table, git_context):
        return None

    if not count_rule_fields_are_consistent(parsed):
        return None

    required_keys = ["src_db", "src_tbl", "src_sql", "dest_sql"]
    if any(not parsed.get(key) for key in required_keys):
        return None

    return {
        "name": "cnt",
        "desc": "总数",
        "src_db": parsed.get("src_db", ""),
        "src_tbl": parsed.get("src_tbl", ""),
        "dest_db": table.get("dest_db") or table.get("db"),
        "dest_tbl": table.get("dest_tbl") or table.get("tbl"),
        "src_sql": parsed.get("src_sql", ""),
        "dest_sql": parsed.get("dest_sql", ""),
        "msg_template": "{dest_tbl} 数量不一致  期望值 {src_value}  实际值{dest_value}  差值为 {diff}",
        "check_field": parsed.get("dest_check_field") or parsed.get("src_check_field"),
        "src_check_field": parsed.get("src_check_field"),
        "dest_check_field": parsed.get("dest_check_field"),
        "ai_reason": parsed.get("reason", ""),
        "git_matches": [item["path"] for item in git_context],
    }
