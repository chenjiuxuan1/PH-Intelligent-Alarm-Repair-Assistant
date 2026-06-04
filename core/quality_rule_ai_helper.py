#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
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


def extract_relevant_git_snippet(text, keywords, window=500, max_chars=1200):
    lower_text = text.lower()
    for keyword in keywords:
        if not keyword:
            continue
        idx = lower_text.find(str(keyword).lower())
        if idx >= 0:
            start = max(0, idx - window)
            end = min(len(text), idx + len(str(keyword)) + window)
            return text[start:end][:max_chars]
    return text[:max_chars]


def choose_best_git_context_path(paths, dest_tbl, src_tbl=None):
    candidates = [Path(p) for p in paths if p]
    if not candidates:
        return []

    def score(path):
        name = path.name.lower()
        stem = path.stem.lower()
        dest = (dest_tbl or '').lower()
        src = (src_tbl or '').lower()
        points = 0
        if path.suffix.lower() == '.sql':
            points += 50
        if stem == dest:
            points += 100
        if src and stem == src:
            points += 80
        if name.startswith('init_'):
            points -= 20
        if dest and dest in name:
            points += 20
        if src and src in name:
            points += 10
        points -= len(name) / 1000
        return points

    best = max(candidates, key=score)
    return [best]


def collect_git_context(dest_tbl, src_tbl=None, git_roots=None, limit=1, preferred_paths=None):
    table_names = [dest_tbl]
    if src_tbl:
        table_names.append(src_tbl)

    snippets = []
    preferred = []
    for candidate in preferred_paths or []:
        try:
            path_obj = Path(candidate)
        except Exception:
            continue
        if path_obj.exists() and path_obj.is_file():
            preferred.append(path_obj)

    preferred = choose_best_git_context_path(preferred, dest_tbl, src_tbl=src_tbl)
    candidate_iter = preferred if preferred else iter_git_candidate_files(git_roots, table_names)
    snippet_keywords = [dest_tbl, src_tbl, ' where ', ' join ', ' from ', 'create_at', 'created_at', 'etl_create_time', 'etl_update_time']
    for path in candidate_iter:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower_text = text.lower()
        if dest_tbl.lower() not in lower_text and (not src_tbl or src_tbl.lower() not in lower_text):
            continue
        snippet = extract_relevant_git_snippet(text, snippet_keywords)
        snippets.append({"path": str(path), "snippet": snippet})
        if len(snippets) >= limit:
            break
    return snippets


def normalize_for_json(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_for_json(item) for item in value]
    return value


def build_ai_messages(database_name, table, git_context, failure_reason):
    system_prompt = (
        "你是一个数仓数据质量规则生成助手。"
        "请根据表信息、Git代码片段和失败原因，生成用于数据质量校验的候选规则。"
        "输出必须是严格 JSON，不要输出 Markdown 代码块，不要输出解释。"
    )
    payload = {
        "database": database_name,
        "table": normalize_for_json(table),
        "failure_reason": failure_reason,
        "git_context": normalize_for_json(git_context),
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
    # Keep the prompt ASCII-safe so remote HTTP/client stacks never try to
    # encode raw CJK characters with latin-1.
    user_prompt = json.dumps(payload, ensure_ascii=True, indent=2)
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
    # Use the HTTP ingestion path consistently. The SDK flush path can block
    # indefinitely on some remote hosts, which makes rule generation appear hung.
    return trace_langfuse_via_http(messages, response_text, parsed_output)


def trace_langfuse_via_http(messages, response_text, parsed_output):
    secret_key = QUALITY_RULE_AI_CONFIG.get("langfuse_secret_key")
    public_key = QUALITY_RULE_AI_CONFIG.get("langfuse_public_key")
    host = (QUALITY_RULE_AI_CONFIG.get("langfuse_base_url") or "").rstrip("/")
    if not secret_key or not public_key or not host:
        return False

    trace_id = uuid.uuid4().hex
    generation_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    basic = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    batch = {
        "batch": [
            {
                "id": uuid.uuid4().hex,
                "timestamp": now,
                "type": "trace-create",
                "body": {
                    "id": trace_id,
                    "name": "quality_rule_ai_fallback",
                    "input": messages,
                    "output": parsed_output,
                },
            },
            {
                "id": uuid.uuid4().hex,
                "timestamp": now,
                "type": "generation-create",
                "body": {
                    "id": generation_id,
                    "traceId": trace_id,
                    "name": "generate_quality_rule_candidate",
                    "model": QUALITY_RULE_AI_CONFIG.get("model"),
                    "input": messages,
                    "output": response_text,
                    "metadata": {"parsed_output": parsed_output},
                },
            },
        ]
    }
    req = urllib.request.Request(
        f"{host}/api/public/ingestion",
        data=json.dumps(batch, ensure_ascii=True).encode("utf-8"),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/json",
            "User-Agent": "PH-Quality-Rule-AI/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.getcode() in (200, 201, 202, 207)
    except Exception:
        return False


def request_openai_compatible_completion_via_sdk(messages):
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(f"openai_sdk_unavailable: {exc}") from exc

    base_url = (QUALITY_RULE_AI_CONFIG.get("base_url") or "").rstrip("/")
    api_key = QUALITY_RULE_AI_CONFIG.get("api_key")
    if not base_url or not api_key:
        raise ValueError("missing base_url or api_key")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=20, max_retries=0)
    completion = client.chat.completions.create(
        model=QUALITY_RULE_AI_CONFIG.get("model"),
        messages=messages,
    )
    choice = (completion.choices or [None])[0]
    message = getattr(choice, "message", None)
    if message is None and isinstance(choice, dict):
        message = choice.get("message")
    if message is None:
        return ""
    if isinstance(message, dict):
        return message.get("content") or ""
    return getattr(message, "content", "") or ""


def request_openai_compatible_completion_via_http(messages):
    payload = {
        "model": QUALITY_RULE_AI_CONFIG.get("model"),
        "messages": messages,
    }
    base_url = (QUALITY_RULE_AI_CONFIG.get("base_url") or "").rstrip("/")
    api_key = QUALITY_RULE_AI_CONFIG.get("api_key")
    if not base_url or not api_key:
        raise ValueError("missing base_url or api_key")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PH-Quality-Rule-AI/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    parsed = json.loads(body)
    choices = parsed.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def request_openai_compatible_completion(messages):
    try:
        return request_openai_compatible_completion_via_sdk(messages)
    except Exception as sdk_exc:
        try:
            return request_openai_compatible_completion_via_http(messages)
        except Exception as http_exc:
            raise RuntimeError(f"sdk_error={sdk_exc}; http_error={http_exc}") from http_exc


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


def build_candidate_from_parsed_output(table, parsed, git_context):
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


def generate_rule_candidate_with_ai(database_name, table, failure_reason, git_roots=None, return_meta=False):
    meta = {
        "attempted": False,
        "status": "not_called",
        "reason": "",
        "git_matches": [],
    }
    if not ai_fallback_available():
        meta["status"] = "ai_not_available"
        meta["reason"] = "AI 或 Langfuse 配置不完整"
        return (None, meta) if return_meta else None
    git_context = collect_git_context(
        table.get("dest_tbl") or table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
        preferred_paths=table.get("git_matches") or [],
    )
    meta["git_matches"] = [item["path"] for item in git_context]
    messages = build_ai_messages(database_name, table, git_context, failure_reason)
    meta["attempted"] = True
    meta["status"] = "requested"
    try:
        response_text = request_openai_compatible_completion(messages)
    except Exception as exc:
        meta["status"] = "ai_request_failed"
        meta["reason"] = str(exc)
        try:
            maybe_trace_langfuse(messages, "", {"status": "ai_request_failed", "reason": str(exc)})
        except Exception:
            pass
        return (None, meta) if return_meta else None
    try:
        parsed = extract_json_object(response_text)
    except Exception as exc:
        meta["status"] = "ai_response_parse_failed"
        meta["reason"] = str(exc)
        return (None, meta) if return_meta else None
    draft_candidate = build_candidate_from_parsed_output(table, parsed, git_context)
    meta["draft_candidate"] = draft_candidate
    traced = maybe_trace_langfuse(messages, response_text, parsed)
    meta["trace_status"] = "ok" if traced else "langfuse_trace_failed"
    if not traced:
        meta["trace_reason"] = "Langfuse trace 未成功写入"

    if not source_field_is_verified(parsed.get("src_check_field"), table, git_context):
        meta["status"] = "ai_output_unverified_source_field"
        meta["reason"] = f"AI 生成的源字段未验证: {parsed.get('src_check_field', '')}"
        return (None, meta) if return_meta else None

    if not count_rule_fields_are_consistent(parsed):
        meta["status"] = "ai_output_inconsistent_fields"
        meta["reason"] = (
            f"AI 生成的字段不一致: {parsed.get('src_check_field', '')} != {parsed.get('dest_check_field', '')}"
        )
        return (None, meta) if return_meta else None

    required_keys = ["src_db", "src_tbl", "src_sql", "dest_sql"]
    if any(not parsed.get(key) for key in required_keys):
        missing = [key for key in required_keys if not parsed.get(key)]
        meta["status"] = "ai_output_missing_keys"
        meta["reason"] = f"AI 返回缺少字段: {', '.join(missing)}"
        return (None, meta) if return_meta else None

    candidate = draft_candidate
    meta["status"] = "ok"
    meta["reason"] = parsed.get("reason", "")
    return (candidate, meta) if return_meta else candidate
