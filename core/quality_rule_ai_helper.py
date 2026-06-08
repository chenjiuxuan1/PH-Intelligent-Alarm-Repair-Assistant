#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from config.config import QUALITY_RULE_AI_CONFIG


CODE_FILE_SUFFIXES = (".sql", ".py", ".scala", ".sh", ".yaml", ".yml", ".json")
_LAST_LANGFUSE_TRACE_ERROR = ""


def _ai_debug_enabled():
    return os.environ.get("QUALITY_RULE_AI_DEBUG", "0") == "1"


def _ai_debug(message):
    if not _ai_debug_enabled():
        return
    print(f"[quality-rule-ai] {message}", file=sys.stderr, flush=True)


class AiRequestTimeoutError(TimeoutError):
    pass


def _ai_deadline_seconds():
    raw = os.environ.get("QUALITY_RULE_AI_DEADLINE_SECONDS", "")
    if raw in ("", None):
        return None
    try:
        value = int(raw)
        if value <= 0:
            return None
        return value
    except Exception:
        return None


def _optional_timeout_seconds(env_name):
    raw = os.environ.get(env_name, "")
    if raw in ("", None):
        return None


def _optional_positive_int(env_name):
    raw = os.environ.get(env_name, "")
    if raw in ("", None):
        return None
    try:
        value = int(raw)
        if value <= 0:
            return None
        return value
    except Exception:
        return None
    try:
        value = float(raw)
        if value <= 0:
            return None
        return value
    except Exception:
        return None


def _run_with_deadline(fn, *args, **kwargs):
    if not hasattr(signal, "SIGALRM"):
        return fn(*args, **kwargs)

    seconds = _ai_deadline_seconds()
    if not seconds:
        return fn(*args, **kwargs)

    def _handle_timeout(signum, frame):
        raise AiRequestTimeoutError(f"ai request exceeded {seconds}s deadline")

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


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
    return not ai_fallback_missing_keys()


def ai_fallback_missing_keys():
    missing = []
    if not QUALITY_RULE_AI_CONFIG.get("enabled"):
        missing.append("enabled")
    if not QUALITY_RULE_AI_CONFIG.get("api_key"):
        missing.append("api_key")
    if not QUALITY_RULE_AI_CONFIG.get("base_url"):
        missing.append("base_url")
    if not QUALITY_RULE_AI_CONFIG.get("model"):
        missing.append("model")
    if not QUALITY_RULE_AI_CONFIG.get("langfuse_secret_key"):
        missing.append("langfuse_secret_key")
    if not QUALITY_RULE_AI_CONFIG.get("langfuse_public_key"):
        missing.append("langfuse_public_key")
    if not QUALITY_RULE_AI_CONFIG.get("langfuse_base_url"):
        missing.append("langfuse_base_url")
    return missing


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


def extract_relevant_git_snippet(text, keywords=None):
    max_chars = _optional_positive_int("QUALITY_RULE_AI_GIT_SNIPPET_MAX_CHARS")
    if not max_chars:
        return text
    if len(text) <= max_chars:
        return text
    _ai_debug(f"git context truncated to {max_chars} chars by QUALITY_RULE_AI_GIT_SNIPPET_MAX_CHARS")
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
    for path in candidate_iter:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower_text = text.lower()
        if dest_tbl.lower() not in lower_text and (not src_tbl or src_tbl.lower() not in lower_text):
            continue
        snippet = extract_relevant_git_snippet(text)
        snippets.append({"path": str(path), "snippet": snippet})
        _ai_debug(f"selected git context file: {path}")
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
        "你是一个资深数仓数据质量规则生成助手。"
        "请根据给定的源表、目标表、失败原因和一段 Git SQL 片段，生成一组可执行的单指标质量校验草稿。"
        "在生成 SQL 之前，你必须先从 ETL SQL 中识别主驱动源表、目标表最终落入的指标来源列、以及真正用于业务切分的事件时间字段。"
        "不要把仅用于维表映射、账号映射、拉链关联、补充属性的辅助表误判为主校验源表。"
        "你不局限于 count(*)，也可以使用 count(distinct ...)、sum(...)、按业务键过滤后的聚合等方式，"
        "如果 ETL 存在拆分、union all、多对一或一对多展开，优先生成金额/成本类 sum 聚合，而不是草率使用 count(*)。"
        "只有当 ETL 证据表明两边天然是一对一明细口径时，count(*) 才是优先选项。"
        "只要源表 SQL 与目标表 SQL 最终都输出一个可比较的数值结果即可。"
        "输出必须是严格 JSON，不要输出 Markdown，不要输出额外解释。"
    )
    raw_columns = table.get("source_columns")
    if raw_columns is None:
        raw_columns = table.get("columns")
    source_columns = normalize_for_json(raw_columns if raw_columns is not None else [])
    dest_columns = normalize_for_json(table.get("dest_columns") or [])
    payload = {
        "task": "generate_metric_rule_candidate",
        "database": database_name,
        "dest_db": table.get("dest_db") or table.get("db") or "",
        "dest_tbl": table.get("dest_tbl") or table.get("tbl") or "",
        "src_db": table.get("src_db") or "",
        "src_tbl": table.get("src_tbl") or "",
        "requested_metric_field": normalize_for_json(table.get("requested_metric_field") or ""),
        "source_columns": source_columns,
        "dest_columns": dest_columns,
        "source_ddl_summary": normalize_for_json(table.get("source_ddl_summary") or ""),
        "dest_ddl_summary": normalize_for_json(table.get("dest_ddl_summary") or ""),
        "failure_reason": failure_reason,
        "validation_feedback": normalize_for_json(table.get("validation_feedback") or {}),
        "git_context": normalize_for_json(git_context),
        "good_examples": [
            {
                "scenario": "源表和目标表都使用同一业务事件时间字段",
                "expected_output": {
                    "src_db": "ods",
                    "src_tbl": "ods_user_order",
                    "src_check_field": "create_at",
                    "dest_check_field": "create_at",
                    "src_sql": "SELECT count(1) AS cnt FROM ods.ods_user_order WHERE create_at >= '{begin}' AND create_at < '{end}'",
                    "dest_sql": "SELECT count(1) AS cnt FROM dwd.dwd_user_order WHERE create_at >= '{begin}' AND create_at < '{end}'",
                    "reason": "源表和目标表都按同一业务事件时间 create_at 过滤，可以直接做同窗口数量校验。"
                }
            },
            {
                "scenario": "源表和目标表都存在同一业务事件时间字段，应优先按该字段做同窗口数量校验",
                "expected_output": {
                    "src_db": "biz_catalog.repay",
                    "src_tbl": "asset",
                    "src_check_field": "asset_create_at",
                    "dest_check_field": "asset_create_at",
                    "src_sql": "SELECT COUNT(*) as cnt FROM biz_catalog.repay.`asset` WHERE asset_create_at >= '{begin}' AND asset_create_at < '{end}'",
                    "dest_sql": "SELECT COUNT(*) as cnt FROM ods.ods_repay_asset WHERE asset_create_at >= '{begin}' AND asset_create_at < '{end}'",
                    "reason": "源表和目标表都存在同一业务事件时间字段 asset_create_at，应优先按相同字段做同窗口数量校验，而不是退回到 ETL 入仓时间。"
                }
            },
            {
                "scenario": "源表存在 create_at，但目标表不存在 create_at；目标表存在业务完成时间 fee_finish_at",
                "expected_output": {
                    "src_db": "ods",
                    "src_tbl": "ods_repay_cpop_income_item",
                    "src_check_field": "create_at",
                    "dest_check_field": "fee_finish_at",
                    "src_sql": "SELECT count(1) AS cnt FROM ods.ods_repay_cpop_income_item WHERE create_at >= '{begin}' AND create_at < '{end}'",
                    "dest_sql": "SELECT count(1) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
                    "reason": "目标表不存在 create_at，但存在业务完成时间 fee_finish_at，应优先使用真实存在且语义最接近的业务时间字段，而不是臆测 create_at。"
                }
            },
            {
                "scenario": "简单 count(*) 不能准确反映两边口径时，可以使用更贴近业务的单指标聚合，只要最终返回一个可比较的数值列",
                "expected_output": {
                    "src_db": "ods",
                    "src_tbl": "ods_payment_order",
                    "src_check_field": "pay_success_at",
                    "dest_check_field": "fee_finish_at",
                    "src_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM ods.ods_payment_order WHERE pay_success_at >= '{begin}' AND pay_success_at < '{end}'",
                    "dest_sql": "SELECT COUNT(DISTINCT order_no) AS cnt FROM dwd.dwd_payment_detail WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
                    "reason": "两边都可以按业务单号去重后做单指标校验；虽然事件字段名称不同，但都是真实存在且语义相近的业务完成时间。"
                }
            },
            {
                "scenario": "ETL 主驱动源表是费用明细表，目标表金额列直接来自源表金额列；映射表只用于补充 account_key 等属性，不应被当成主校验源表",
                "expected_output": {
                    "src_db": "ods",
                    "src_tbl": "ods_paysvr_fee",
                    "src_check_field": "fee_finish_at",
                    "dest_check_field": "fee_finish_at",
                    "src_sql": "SELECT COALESCE(ROUND(SUM(fee_amount), 6), 0) AS cnt FROM ods.ods_paysvr_fee WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
                    "dest_sql": "SELECT COALESCE(ROUND(SUM(total_cost), 6), 0) AS cnt FROM dwd_sec.dwd_cst_pay_cost_detail WHERE fee_finish_at >= '{begin}' AND fee_finish_at < '{end}'",
                    "reason": "Git SQL 显示目标表主驱动明细来自 ods_paysvr_fee，fee_finish_at 是共同业务时间，total_cost 来源于 fee_amount。由于 ETL 存在拆分和 union all，优先用金额汇总而不是 count(*)，并统一 ROUND 精度避免小数尾差。"
                }
            }
        ],
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
            "生成的是单指标校验规则草稿，必须返回 src_sql 和 dest_sql，并且两边 SQL 都必须输出一个可比较的数值列，列别名统一为 cnt。",
            "优先从 git SQL 片段中识别源表、目标表、业务键和事件限制字段。",
            "如果 Git SQL 显示某个表只用于映射、补充属性、拉链关联、account_key 关联、维表 enrichment，不要把它当成主驱动源表。",
            "优先从最终 insert into 目标表之前的主明细来源，倒推出真正的源表和指标来源列。",
            "优先选择业务事件时间字段，例如 create_at、created_at、create_time、order_time、finish_at、success_at 等。",
            "SQL 中如果使用时间窗口，必须保留 {begin} 和 {end} 占位符。",
            "src_check_field 必须真实存在于 source_columns 或 source_ddl_summary 或 Git 片段明确提到的源表字段中，否则禁止输出。",
            "dest_check_field 必须真实存在于 dest_columns 或 dest_ddl_summary 中，否则禁止输出。",
            "不要臆测源表存在 etl_create_time/etl_update_time，除非 Git 片段或 source_columns 明确出现。",
            "如果简单 count(*) 不能合理校验两边数据，可以改用 count(distinct 业务键)、sum(金额)、或带业务过滤条件的单值聚合，但不要输出多列或明细结果。",
            "如果目标表某个金额列、成本列、数量列是由源表某个字段直接映射或拆分汇总而来，优先校验这类同语义聚合，例如 sum(source_amount) 对 sum(target_amount)。",
            "如果 requested_metric_field 非空，必须优先围绕该字段生成校验 SQL；不要忽略它退回默认 count(*) 或 if_exists。",
            "如果 requested_metric_field 指向目标侧金额/数量/成本字段，应优先追溯源侧语义对应字段，并生成该字段相关的单值聚合 SQL。",
            "如果 ETL 包含 split、rate、比例分摊、union all、多笔拆分等迹象，默认 count(*) 不可靠，应优先考虑 sum(金额) 或 count(distinct 业务键)。",
            "如果选择金额、成本、汇率换算后的数值聚合，必须统一两边精度，例如 ROUND(SUM(...), 6) 或 CAST 为相同 DECIMAL 精度，避免 0.000001 级别尾差导致误判。",
            "如果源表和目标表字段同语义，应优先给出相同字段名。",
            "如果 Git 片段或元数据表明源表和目标表都存在同一业务事件字段，必须优先使用同一个字段名，不要退回到目标表的 etl_create_time。",
            "如果目标表不存在同名业务时间字段，应优先选择 dest_columns 中真实存在且语义最接近的业务时间字段，例如 finish_at、paid_at、success_at、completed_at。",
            "只有在目标表不存在可确认的业务事件时间字段时，才允许退回 etl_create_time 或 etl_update_time，并在 reason 中说明原因。",
            "如果在现有证据下无法证明源表和目标表存在同一个可验证的业务限制字段，不要硬写一个貌似可跑的错误 SQL；应返回最合理草稿，并在 reason 中明确指出证据不足或映射未证实。",
            "如果源表和目标表最终使用不同字段，也必须返回 src_check_field、dest_check_field、src_sql 和 dest_sql 草稿，并在 reason 中解释差异原因。",
            "如果无法完全确定，也返回最合理草稿，并在 reason 中简要说明依据。",
            "如果 validation_feedback 明确指出上一版 SQL 虽然可运行但结果不一致，请优先根据该反馈修正校验口径，而不是重复输出相同 SQL。",
            "如果 validation_feedback 明确指出某个字段不存在或不应使用，禁止再次输出该字段。",
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


def sanitize_messages_for_langfuse(messages):
    sanitized = []
    for message in messages or []:
        item = dict(message or {})
        content = item.get("content")
        if not isinstance(content, str):
            sanitized.append(item)
            continue
        try:
            payload = json.loads(content)
        except Exception:
            sanitized.append(item)
            continue
        minimal_payload = {
            "task": payload.get("task", ""),
            "database": payload.get("database", ""),
            "dest_db": payload.get("dest_db", ""),
            "dest_tbl": payload.get("dest_tbl", ""),
            "src_db": payload.get("src_db", ""),
            "src_tbl": payload.get("src_tbl", ""),
            "failure_reason": payload.get("failure_reason", ""),
            "validation_feedback": payload.get("validation_feedback", {}),
            "git_context": [],
        }
        git_context = payload.get("git_context")
        if isinstance(git_context, list):
            minimal_payload["git_context"] = [
                {"path": entry.get("path", "")}
                for entry in git_context
                if isinstance(entry, dict) and entry.get("path")
            ]
        item["content"] = json.dumps(minimal_payload, ensure_ascii=True, indent=2)
        sanitized.append(item)
    return sanitized


def build_langfuse_ingestion_batch(messages, response_text, parsed_output):
    trace_id = uuid.uuid4().hex
    generation_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    observation_messages = sanitize_messages_for_langfuse(messages)
    return {
        "batch": [
            {
                "id": uuid.uuid4().hex,
                "timestamp": now,
                "type": "trace-create",
                "body": {
                    "id": trace_id,
                    "name": "quality_rule_ai_fallback",
                    "input": observation_messages,
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
                    "input": observation_messages,
                    "output": response_text,
                    "metadata": {"parsed_output": parsed_output},
                },
            },
        ]
    }


def export_langfuse_ingestion_batch(batch, export_path):
    if not export_path:
        return None
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    _ai_debug(f"exported Langfuse batch to {path}")
    return str(path)


def trace_langfuse_via_http(messages, response_text, parsed_output):
    global _LAST_LANGFUSE_TRACE_ERROR
    secret_key = QUALITY_RULE_AI_CONFIG.get("langfuse_secret_key")
    public_key = QUALITY_RULE_AI_CONFIG.get("langfuse_public_key")
    host = (QUALITY_RULE_AI_CONFIG.get("langfuse_base_url") or "").rstrip("/")
    if not secret_key or not public_key or not host:
        _LAST_LANGFUSE_TRACE_ERROR = "missing_langfuse_credentials"
        return False

    basic = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    batch = build_langfuse_ingestion_batch(messages, response_text, parsed_output)
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
        _ai_debug(f"writing Langfuse trace to {host}/api/public/ingestion")
        timeout_seconds = _optional_timeout_seconds("QUALITY_RULE_LANGFUSE_TIMEOUT_SECONDS")
        if timeout_seconds:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout_seconds)
        else:
            resp_ctx = urllib.request.urlopen(req)
        with resp_ctx as resp:
            ok = resp.getcode() in (200, 201, 202, 207)
            if not ok:
                _LAST_LANGFUSE_TRACE_ERROR = f"unexpected_status={resp.getcode()}"
                _ai_debug(f"langfuse trace unexpected status: {resp.getcode()}")
            else:
                _LAST_LANGFUSE_TRACE_ERROR = ""
                _ai_debug(f"langfuse trace accepted: {resp.getcode()}")
            return ok
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = "<unreadable>"
        _LAST_LANGFUSE_TRACE_ERROR = f"http_{exc.code}: {detail}"
        _ai_debug(f"langfuse trace http error {exc.code}: {detail}")
        return False
    except Exception as exc:
        _LAST_LANGFUSE_TRACE_ERROR = repr(exc)
        _ai_debug(f"langfuse trace failed: {exc}")
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

    client_kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "max_retries": 0,
    }
    timeout_seconds = _optional_timeout_seconds("QUALITY_RULE_AI_SDK_TIMEOUT_SECONDS")
    if timeout_seconds:
        client_kwargs["timeout"] = timeout_seconds
    client = OpenAI(**client_kwargs)
    started_at = time.time()
    _ai_debug("calling DashScope via OpenAI SDK")
    completion = client.chat.completions.create(
        model=QUALITY_RULE_AI_CONFIG.get("model"),
        messages=messages,
    )
    _ai_debug(f"DashScope SDK returned in {time.time() - started_at:.2f}s")
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
    started_at = time.time()
    _ai_debug("calling DashScope via HTTP fallback")
    try:
        timeout_seconds = _optional_timeout_seconds("QUALITY_RULE_AI_HTTP_TIMEOUT_SECONDS")
        if timeout_seconds:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout_seconds)
        else:
            resp_ctx = urllib.request.urlopen(req)
        with resp_ctx as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    _ai_debug(f"DashScope HTTP returned in {time.time() - started_at:.2f}s")
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
    raw_columns = table.get("source_columns")
    if raw_columns is None:
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

    ddl_summary = str(table.get("source_ddl_summary") or "")
    if ddl_summary and re.search(rf"`?{re.escape(normalized)}`?\b", ddl_summary, re.IGNORECASE):
        return True

    for item in git_context or []:
        snippet = (item or {}).get("snippet", "")
        if re.search(rf"\b{re.escape(normalized)}\b", snippet, re.IGNORECASE):
            return True
    return False


def dest_field_is_verified(field_name, table):
    if not field_name:
        return False
    normalized = str(field_name).lower()
    raw_columns = table.get("dest_columns")
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

    ddl_summary = str(table.get("dest_ddl_summary") or "")
    if ddl_summary and re.search(rf"`?{re.escape(normalized)}`?\b", ddl_summary, re.IGNORECASE):
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
    global _LAST_LANGFUSE_TRACE_ERROR
    meta = {
        "attempted": False,
        "status": "not_called",
        "reason": "",
        "git_matches": [],
    }
    missing_keys = ai_fallback_missing_keys()
    if missing_keys:
        meta["status"] = "ai_not_available"
        meta["reason"] = f"AI 或 Langfuse 配置不完整: {', '.join(missing_keys)}"
        _ai_debug(f"ai fallback unavailable, missing keys: {', '.join(missing_keys)}")
        return (None, meta) if return_meta else None
    _ai_debug(
        "ai fallback start "
        f"database={database_name} dest_tbl={table.get('dest_tbl') or table.get('tbl')} "
        f"src_tbl={table.get('src_tbl') or ''}"
    )
    git_context = collect_git_context(
        table.get("dest_tbl") or table.get("tbl") or "",
        src_tbl=table.get("src_tbl"),
        git_roots=git_roots,
        preferred_paths=table.get("git_matches") or [],
    )
    meta["git_matches"] = [item["path"] for item in git_context]
    _ai_debug(f"git context count={len(git_context)}")
    messages = build_ai_messages(database_name, table, git_context, failure_reason)
    meta["attempted"] = True
    meta["status"] = "requested"
    try:
        response_text = _run_with_deadline(request_openai_compatible_completion, messages)
    except AiRequestTimeoutError as exc:
        meta["status"] = "ai_request_timeout"
        meta["reason"] = str(exc)
        _ai_debug(f"ai request timeout: {exc}")
        try:
            maybe_trace_langfuse(messages, "", {"status": "ai_request_timeout", "reason": str(exc)})
        except Exception:
            pass
        return (None, meta) if return_meta else None
    except Exception as exc:
        meta["status"] = "ai_request_failed"
        meta["reason"] = str(exc)
        _ai_debug(f"ai request failed: {exc}")
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
        _ai_debug(f"ai response parse failed: {exc}")
        return (None, meta) if return_meta else None
    draft_candidate = build_candidate_from_parsed_output(table, parsed, git_context)
    meta["draft_candidate"] = draft_candidate
    _LAST_LANGFUSE_TRACE_ERROR = ""
    export_path = os.environ.get("QUALITY_RULE_LANGFUSE_EXPORT_PATH", "").strip()
    traced = maybe_trace_langfuse(messages, response_text, parsed)
    meta["trace_status"] = "ok" if traced else "langfuse_trace_failed"
    if not traced:
        meta["trace_reason"] = _LAST_LANGFUSE_TRACE_ERROR or "Langfuse trace 未成功写入"
        _ai_debug(f"langfuse trace failed: {meta['trace_reason']}")
        if export_path:
            batch = build_langfuse_ingestion_batch(messages, response_text, parsed)
            meta["trace_export_path"] = export_langfuse_ingestion_batch(batch, export_path)
    else:
        _ai_debug("langfuse trace ok")

    if not source_field_is_verified(parsed.get("src_check_field"), table, git_context):
        meta["status"] = "ai_output_unverified_source_field"
        meta["reason"] = f"AI 生成的源字段未验证: {parsed.get('src_check_field', '')}"
        _ai_debug(meta["reason"])
        return (None, meta) if return_meta else None

    if not dest_field_is_verified(parsed.get("dest_check_field"), table):
        meta["status"] = "ai_output_unverified_dest_field"
        meta["reason"] = f"AI 生成的目标字段未验证: {parsed.get('dest_check_field', '')}"
        _ai_debug(meta["reason"])
        return (None, meta) if return_meta else None

    required_keys = ["src_db", "src_tbl", "src_sql", "dest_sql"]
    if any(not parsed.get(key) for key in required_keys):
        missing = [key for key in required_keys if not parsed.get(key)]
        meta["status"] = "ai_output_missing_keys"
        meta["reason"] = f"AI 返回缺少字段: {', '.join(missing)}"
        _ai_debug(meta["reason"])
        return (None, meta) if return_meta else None

    candidate = draft_candidate
    meta["status"] = "ok"
    meta["reason"] = parsed.get("reason", "")
    _ai_debug("ai fallback success")
    return (candidate, meta) if return_meta else candidate
