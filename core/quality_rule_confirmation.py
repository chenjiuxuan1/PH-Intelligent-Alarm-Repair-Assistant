#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Iterable

from config.config import QUALITY_RULE_FORM_CONFIG, WORKSPACE_CONFIG
from core.quality_rule_gap_scanner import resolve_rule_name
from core.send_tv_report import send_tv_report


QUALITY_RULE_BACKLOG_FILE = WORKSPACE_CONFIG["quality_rule_backlog_file"]
QUALITY_RULE_SYNC_STATE_FILE = WORKSPACE_CONFIG["quality_rule_sync_state_file"]


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_json_file(path, default):
    target = Path(path)
    if not target.exists():
        return default
    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def save_json_file(path, payload):
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_backlog():
    data = load_json_file(QUALITY_RULE_BACKLOG_FILE, {"items": {}})
    data.setdefault("items", {})
    return data


def save_backlog(backlog):
    save_json_file(QUALITY_RULE_BACKLOG_FILE, backlog)


def load_sync_state():
    data = load_json_file(QUALITY_RULE_SYNC_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_sync_state(state):
    save_json_file(QUALITY_RULE_SYNC_STATE_FILE, state)


def build_candidate_key(item):
    return f"{item['database']}::{item['dest_db']}.{item['dest_tbl']}::{item['rule_name']}"


def normalize_requested_metric_field(value):
    return (value or "").strip()


def candidate_to_backlog_item(item, detected_at=None):
    detected_at = detected_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    candidate = item["candidate"]
    return {
        "candidate_key": build_candidate_key(item),
        "country": item.get("country") or QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
        "status": "pending_confirmation",
        "database": item["database"],
        "rule_name": item["rule_name"],
        "dest_db": item["dest_db"],
        "dest_tbl": item["dest_tbl"],
        "src_db": candidate.get("src_db", ""),
        "src_tbl": candidate.get("src_tbl", ""),
        "check_field": candidate.get("check_field") or "",
        "requested_metric_field": normalize_requested_metric_field(candidate.get("requested_metric_field")),
        "src_sql": candidate.get("src_sql", ""),
        "dest_sql": candidate.get("dest_sql", ""),
        "reason": item.get("reason", ""),
        "git_matches": candidate.get("git_matches", []),
        "validation_status": item.get("validation_status", ""),
        "validation_reason": item.get("validation_reason", ""),
        "validation_error": item.get("validation_error", ""),
        "validation_window_begin": item.get("validation_window_begin", ""),
        "validation_window_end": item.get("validation_window_end", ""),
        "src_value": item.get("src_value"),
        "dest_value": item.get("dest_value"),
        "diff": item.get("diff"),
        "ai_status": item.get("ai_status", ""),
        "ai_retry_count": item.get("ai_retry_count", 0),
        "detected_at": detected_at,
        "scan_status": item.get("status", "candidate"),
        "notified_at": None,
        "form_submitted_at": None,
        "last_form_payload_signature": "",
        "decision": "",
        "decision_notes": "",
        "decision_operator": "",
        "decision_submitted_at": "",
        "decision_requested_metric_field": "",
        "applied_at": "",
    }


def result_to_backlog_item(item, detected_at=None):
    if item.get("candidate"):
        return candidate_to_backlog_item(item, detected_at=detected_at)

    detected_at = detected_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "candidate_key": build_candidate_key(item),
        "country": item.get("country") or QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
        "status": "pending_confirmation",
        "database": item["database"],
        "rule_name": item["rule_name"],
        "dest_db": item["dest_db"],
        "dest_tbl": item["dest_tbl"],
        "src_db": item.get("src_db", ""),
        "src_tbl": item.get("src_tbl", ""),
        "check_field": item.get("check_field") or "",
        "requested_metric_field": normalize_requested_metric_field(item.get("requested_metric_field")),
        "src_sql": item.get("src_sql", ""),
        "dest_sql": item.get("dest_sql", ""),
        "reason": item.get("reason", ""),
        "git_matches": item.get("git_matches", []),
        "validation_status": item.get("validation_status", ""),
        "validation_reason": item.get("validation_reason", ""),
        "validation_error": item.get("validation_error", ""),
        "validation_window_begin": item.get("validation_window_begin", ""),
        "validation_window_end": item.get("validation_window_end", ""),
        "src_value": item.get("src_value"),
        "dest_value": item.get("dest_value"),
        "diff": item.get("diff"),
        "ai_status": item.get("ai_status", ""),
        "ai_retry_count": item.get("ai_retry_count", 0),
        "detected_at": detected_at,
        "scan_status": item.get("status", "blocked"),
        "notified_at": None,
        "form_submitted_at": None,
        "last_form_payload_signature": "",
        "decision": "",
        "decision_notes": "",
        "decision_operator": "",
        "decision_submitted_at": "",
        "decision_requested_metric_field": "",
        "applied_at": "",
    }


def merge_candidates_into_backlog(results, backlog=None, detected_at=None):
    backlog = backlog or load_backlog()
    items = backlog.setdefault("items", {})
    new_items = []
    refreshable_statuses = {"pending_confirmation", "candidate", "blocked", ""}
    for item in results:
        key = build_candidate_key(item)
        if item.get("status") in {"existing", "skipped"}:
            existing = items.get(key)
            if existing and existing.get("status") in refreshable_statuses:
                existing["status"] = item.get("status")
                existing["reason"] = item.get("reason", existing.get("reason", ""))
                existing["scan_status"] = item.get("status")
                existing["rescan_at"] = detected_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            continue

        backlog_item = result_to_backlog_item(item, detected_at=detected_at)
        if key not in items:
            items[key] = backlog_item
            new_items.append(backlog_item)
            continue

        existing = items[key]
        if existing.get("status") in refreshable_statuses:
            preserved = {
                "notified_at": existing.get("notified_at"),
                "form_submitted_at": existing.get("form_submitted_at"),
                "last_form_payload_signature": existing.get("last_form_payload_signature", ""),
                "decision": existing.get("decision", ""),
                "decision_notes": existing.get("decision_notes", ""),
                "decision_operator": existing.get("decision_operator", ""),
                "decision_submitted_at": existing.get("decision_submitted_at", ""),
                "decision_src_sql": existing.get("decision_src_sql", ""),
                "decision_dest_sql": existing.get("decision_dest_sql", ""),
                "decision_human_check": existing.get("decision_human_check", ""),
                "decision_requested_metric_field": existing.get("decision_requested_metric_field", ""),
                "requested_metric_field": existing.get("requested_metric_field", ""),
                "applied_at": existing.get("applied_at", ""),
                "rescan_at": detected_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            backlog_item.update(preserved)
            items[key] = backlog_item
    return backlog, new_items


def compute_form_payload_signature(backlog_item):
    payload = build_detection_form_payload(backlog_item)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def backlog_item_has_submittable_sql(item):
    rule_name = (item.get("rule_name") or "").strip().lower()
    src_sql = (item.get("src_sql") or "").strip()
    dest_sql = (item.get("dest_sql") or "").strip()
    if rule_name == "if_exists":
        return bool(dest_sql)
    if rule_name == "cnt":
        return bool(src_sql and dest_sql)
    return bool(dest_sql)


def get_pending_form_submission_items(backlog, include_submitted=False):
    items = backlog.get("items", {}).values()
    pending_items = []
    for item in items:
        if item.get("status") != "pending_confirmation":
            continue
        if not backlog_item_has_submittable_sql(item):
            continue
        if not item.get("form_submitted_at"):
            pending_items.append(item)
            continue
        if not include_submitted:
            continue
        current_signature = compute_form_payload_signature(item)
        previous_signature = item.get("last_form_payload_signature", "")
        if current_signature != previous_signature:
            pending_items.append(item)
    pending_items.sort(key=lambda item: (item.get("detected_at", ""), item.get("candidate_key", "")))
    return pending_items


def format_tv_confirmation_message(new_items, confirmation_sheet_url=""):
    lines = []
    lines.append("📋 质量规则待补充确认")
    lines.append("")
    lines.append(f"本次新增待确认规则: {len(new_items)}")
    lines.append("")
    for item in new_items[:20]:
        lines.append(f"• {item['country']} / {item['database']} / {item['dest_tbl']}")
    if len(new_items) > 20:
        lines.append(f"... 其余 {len(new_items) - 20} 条见待确认池")
    if confirmation_sheet_url:
        lines.append("")
        lines.append(f"确认响应表: {confirmation_sheet_url}")
        lines.append("请在响应表中按需修改 src_sql / dest_sql，并设置 need_apply：1=补充，0=关闭该表自动校验。")
        lines.append("确认无误后将 human_check 改为 1，系统才会执行。")
    return "\n".join(lines)


def notify_new_candidates_via_tv(new_items, mentions=None, confirmation_sheet_url=None):
    if not new_items:
        return {"success": True, "skipped": True, "reason": "no_new_candidates"}
    message = format_tv_confirmation_message(
        new_items,
        confirmation_sheet_url=confirmation_sheet_url or QUALITY_RULE_FORM_CONFIG.get("confirmation_sheet_url", ""),
    )
    return send_tv_report(
        message,
        mentions=mentions or QUALITY_RULE_FORM_CONFIG.get("notify_mentions", []),
        bot_id=QUALITY_RULE_FORM_CONFIG.get("notify_bot_id"),
    )


def extract_hidden_fields(html_text: str) -> dict:
    names = ["fvv", "pageHistory", "fbzx", "submissionTimestamp", "partialResponse"]
    extracted = {}
    for name in names:
        match = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html_text)
        if match:
            extracted[name] = html.unescape(match.group(1))
    extracted.setdefault("fvv", "1")
    extracted.setdefault("pageHistory", "0")
    extracted.setdefault("submissionTimestamp", "-1")
    return extracted


def fetch_viewform(view_url):
    req = urllib.request.Request(view_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def build_form_payload(payload, field_map, hidden=None, required_fields=None):
    hidden = hidden or {}
    required_fields = set(required_fields or [])
    missing = sorted(field for field in required_fields if not payload.get(field))
    if missing:
        raise ValueError(f"missing required payload fields: {', '.join(missing)}")

    form = {}
    for field, value in payload.items():
        entry_id = field_map.get(field)
        if not entry_id or value is None:
            continue
        form[entry_id] = str(value)
    form.update(hidden)
    return form


def submit_google_form(view_url, post_url, field_map, payload, required_fields=None, dry_run=False):
    html_text = fetch_viewform(view_url)
    hidden = extract_hidden_fields(html_text)
    form_payload = build_form_payload(payload, field_map, hidden=hidden, required_fields=required_fields)

    if dry_run:
        return {"ok": True, "dry_run": True, "payload": form_payload}

    data = urllib.parse.urlencode(form_payload).encode("utf-8")
    req = urllib.request.Request(
        post_url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
            "Referer": view_url,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "matched_success_text": False,
            "matched_confirm_hint": False,
            "body_preview": body[:500],
            "error": f"HTTPError {exc.code}",
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status": None,
            "matched_success_text": False,
            "matched_confirm_hint": False,
            "body_preview": "",
            "error": f"URLError {exc}",
        }
    has_cn_success = "您的回复已记录" in body
    has_en_success = "Your response has been recorded" in body
    has_confirm_hint = "form_confirm" in body or "另填写一份回复" in body
    ok = status == 200 and (has_cn_success or has_en_success or has_confirm_hint)
    return {
        "ok": ok,
        "status": status,
        "matched_success_text": has_cn_success or has_en_success,
        "matched_confirm_hint": has_confirm_hint,
        "body_preview": body[:500] if not ok else "",
    }


def build_detection_form_payload(backlog_item, submitter="codex", cluster_or_env="wattrel"):
    return {
        "submission_type": "detected",
        "candidate_key": backlog_item["candidate_key"],
        "submitter": submitter,
        "country": backlog_item.get("country") or QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
        "cluster_or_env": cluster_or_env,
        "database": backlog_item["database"],
        "tbl": backlog_item["dest_tbl"],
        "need_apply": "1",
        "src_sql": backlog_item.get("src_sql", ""),
        "dest_sql": backlog_item.get("dest_sql", ""),
        "human_check": "0",
    }


def submit_backlog_items_to_form(backlog_items, form_config=None, dry_run=False):
    form_config = form_config or QUALITY_RULE_FORM_CONFIG
    view_url = form_config.get("view_url")
    post_url = form_config.get("post_url")
    field_map = form_config.get("field_map") or {}
    required_fields = form_config.get("required_fields") or []
    if not view_url or not post_url or not field_map:
        return {"submitted": 0, "results": [], "skipped": True, "reason": "form_config_incomplete"}

    results = []
    submitted = 0
    for item in backlog_items:
        payload = build_detection_form_payload(item)
        result = submit_google_form(
            view_url,
            post_url,
            field_map,
            payload,
            required_fields=required_fields,
            dry_run=dry_run,
        )
        result["candidate_key"] = item["candidate_key"]
        results.append(result)
        if result.get("ok"):
            submitted += 1
    return {"submitted": submitted, "results": results, "skipped": False}


def fetch_confirmation_csv(export_url):
    req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_spreadsheet_id_from_url(url):
    if not url:
        return ""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    return match.group(1) if match else ""


def _extract_sheet_gid_from_url(url):
    if not url:
        return ""
    match = re.search(r"[?&#]gid=(\d+)", url)
    return match.group(1) if match else ""


def delete_confirmation_sheet_rows(row_numbers: Iterable[int], form_config=None, dry_run=False):
    form_config = form_config or QUALITY_RULE_FORM_CONFIG
    normalized_rows = sorted(
        {
            int(str(row_number).strip())
            for row_number in (row_numbers or [])
            if row_number not in (None, "")
            and str(row_number).strip().isdigit()
            and int(str(row_number).strip()) >= 2
        },
        reverse=True,
    )
    if not normalized_rows:
        return {
            "success": True,
            "skipped": True,
            "reason": "no_rows",
            "deleted_rows": [],
        }

    spreadsheet_id = (
        (form_config.get("confirmation_spreadsheet_id") or "").strip()
        or _extract_spreadsheet_id_from_url(form_config.get("confirmation_sheet_url", ""))
        or _extract_spreadsheet_id_from_url(form_config.get("confirmation_export_url", ""))
    )
    sheet_gid_text = (
        (form_config.get("confirmation_sheet_gid") or "").strip()
        or _extract_sheet_gid_from_url(form_config.get("confirmation_sheet_url", ""))
        or _extract_sheet_gid_from_url(form_config.get("confirmation_export_url", ""))
    )
    credentials_json = (form_config.get("confirmation_google_service_account_json") or "").strip()
    credentials_file = (form_config.get("confirmation_google_service_account_file") or "").strip()

    if not spreadsheet_id or not sheet_gid_text:
        return {
            "success": False,
            "skipped": True,
            "reason": "sheet_config_incomplete",
            "deleted_rows": [],
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid_text,
        }
    if not credentials_json and not credentials_file:
        return {
            "success": False,
            "skipped": True,
            "reason": "google_credentials_missing",
            "deleted_rows": [],
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid_text,
        }

    try:
        sheet_gid = int(sheet_gid_text)
    except (TypeError, ValueError):
        return {
            "success": False,
            "skipped": True,
            "reason": "invalid_sheet_gid",
            "deleted_rows": [],
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid_text,
        }

    if dry_run:
        return {
            "success": True,
            "skipped": False,
            "dry_run": True,
            "deleted_rows": normalized_rows,
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid,
        }

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception as exc:
        return {
            "success": False,
            "skipped": True,
            "reason": "google_client_library_missing",
            "error": str(exc),
            "deleted_rows": [],
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid,
        }

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        if credentials_json:
            credentials_info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=scopes)
        else:
            credentials = service_account.Credentials.from_service_account_file(credentials_file, scopes=scopes)

        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        requests = [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_gid,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number,
                    }
                }
            }
            for row_number in normalized_rows
        ]
        response = (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
            .execute()
        )
        return {
            "success": True,
            "skipped": False,
            "deleted_rows": normalized_rows,
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid,
            "response": response,
        }
    except Exception as exc:
        return {
            "success": False,
            "skipped": False,
            "error": str(exc),
            "deleted_rows": [],
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid,
        }


def parse_confirmation_rows(csv_text, column_map):
    reader = csv.DictReader(csv_text.splitlines())
    rows = []
    for row_number, raw_row in enumerate(reader, start=2):
        item = {}
        for logical_key, column_name in column_map.items():
            item[logical_key] = raw_row.get(column_name, "").strip()
        item["sheet_row_number"] = row_number
        rows.append(item)
    return rows


def extract_sheet_row_number(row):
    for key in ("decision_sheet_row_number", "sheet_row_number", "row_number", "_rowNumber", "rowIndex", "__sheet_row_number"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            row_number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if row_number >= 2:
            return row_number
    return None


def build_decision_signature(row):
    candidate_key = infer_candidate_key_from_row(row)
    submitted_at = (row.get("submitted_at") or "").strip()
    need_apply = (row.get("need_apply") or "").strip()
    metric_field = normalize_requested_metric_field(row.get("metric_field"))
    src_sql = (row.get("src_sql") or "").strip()
    dest_sql = (row.get("dest_sql") or "").strip()
    human_check = (row.get("human_check") or "").strip()
    return "||".join([candidate_key, submitted_at, need_apply, human_check, metric_field, src_sql, dest_sql])


def filter_unprocessed_decision_rows(rows, sync_state=None):
    sync_state = sync_state or {}
    processed = sync_state.get("processed_decisions", {})
    filtered = []
    for row in rows:
        signature = build_decision_signature(row)
        if not signature or signature in processed:
            continue
        filtered.append(row)
    return filtered


def need_apply_is_enabled(value):
    normalized = (value or "").strip().lower()
    return normalized in {"1", "yes", "y", "true", "apply", "补充", "确认补充"}


def human_check_is_enabled(value):
    normalized = (value or "").strip().lower()
    return normalized in {"1", "yes", "y", "true", "pass", "通过", "确认"}


def infer_candidate_key_from_row(row):
    existing = (row.get("candidate_key") or "").strip()
    if existing:
        return existing

    database = (row.get("database") or "").strip()
    dest_tbl = (row.get("tbl") or row.get("dest_tbl") or "").strip()
    if not database or not dest_tbl:
        return ""

    rule_name = resolve_rule_name(database) if database else ""
    return f"{database}::{database}.{dest_tbl}::{rule_name}"


def latest_decisions_by_candidate(rows):
    decisions = {}
    for row in rows:
        key = infer_candidate_key_from_row(row)
        if not key:
            continue
        row["candidate_key"] = key
        current = decisions.get(key)
        submitted_at = row.get("submitted_at", "")
        if current is None or submitted_at >= current.get("submitted_at", ""):
            decisions[key] = row
    return decisions


def find_latest_requested_metric_field(rows, database, tbl):
    database = (database or "").strip()
    tbl = (tbl or "").strip()
    latest_row = None
    for row in rows:
        row_database = (row.get("database") or "").strip()
        row_tbl = (row.get("tbl") or row.get("dest_tbl") or "").strip()
        metric_field = normalize_requested_metric_field(row.get("metric_field"))
        if not metric_field:
            continue
        if row_database != database or row_tbl != tbl:
            continue
        submitted_at = (row.get("submitted_at") or "").strip()
        if latest_row is None or submitted_at >= (latest_row.get("submitted_at") or ""):
            latest_row = row
    if latest_row is None:
        return ""
    return normalize_requested_metric_field(latest_row.get("metric_field"))


def find_latest_confirmation_row(rows, database, tbl, country=""):
    database = (database or "").strip()
    tbl = (tbl or "").strip()
    country = (country or "").strip().lower()
    latest_row = None
    for row in rows:
        row_database = (row.get("database") or "").strip()
        row_tbl = (row.get("tbl") or row.get("dest_tbl") or "").strip()
        row_country = str(
            row.get("country") or QUALITY_RULE_FORM_CONFIG.get("country", "ph")
        ).strip().lower()
        if row_database != database or row_tbl != tbl:
            continue
        if country and row_country != country:
            continue
        submitted_at = (row.get("submitted_at") or "").strip()
        if latest_row is None or submitted_at >= (latest_row.get("submitted_at") or ""):
            latest_row = row
    return latest_row


def update_backlog_with_decisions(backlog, decision_rows):
    items = backlog.setdefault("items", {})
    latest = latest_decisions_by_candidate(decision_rows)
    approved = []
    rejected = []
    for key, row in latest.items():
        if key not in items:
            continue
        decision_value = (row.get("need_apply", "") or "").strip()
        if not decision_value:
            continue
        human_check_value = (row.get("human_check", "") or "").strip()
        if human_check_value and not human_check_is_enabled(human_check_value):
            continue
        item = items[key]
        item["decision"] = row.get("need_apply", "")
        item["decision_notes"] = row.get("notes", "")
        item["decision_operator"] = row.get("operator", "")
        item["decision_submitted_at"] = row.get("submitted_at", "")
        item["decision_src_sql"] = row.get("src_sql", "")
        item["decision_dest_sql"] = row.get("dest_sql", "")
        item["decision_requested_metric_field"] = normalize_requested_metric_field(row.get("metric_field"))
        if item["decision_requested_metric_field"]:
            item["requested_metric_field"] = item["decision_requested_metric_field"]
        item["decision_human_check"] = row.get("human_check", "")
        item["decision_sheet_row_number"] = extract_sheet_row_number(row)
        item["decision_signature"] = build_decision_signature(row)
        item["status"] = "approved" if need_apply_is_enabled(item["decision"]) else "rejected"
        if item["status"] == "approved":
            approved.append(item)
        else:
            rejected.append(item)
    return approved, rejected


def mark_processed_decisions(sync_state, items, action):
    if not items:
        return sync_state
    sync_state = sync_state or {}
    processed = sync_state.setdefault("processed_decisions", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item in items:
        signature = item.get("decision_signature", "")
        if not signature:
            signature = build_decision_signature(
                {
                    "candidate_key": item.get("candidate_key", ""),
                    "submitted_at": item.get("decision_submitted_at", ""),
                    "need_apply": item.get("decision", ""),
                    "human_check": item.get("decision_human_check", ""),
                    "metric_field": item.get("decision_requested_metric_field") or item.get("requested_metric_field", ""),
                    "src_sql": item.get("decision_src_sql") or item.get("src_sql", ""),
                    "dest_sql": item.get("decision_dest_sql") or item.get("dest_sql", ""),
                    "database": item.get("database", ""),
                    "tbl": item.get("dest_tbl", ""),
                    "dest_tbl": item.get("dest_tbl", ""),
                }
            )
        if not signature:
            continue
        processed[signature] = {
            "candidate_key": item.get("candidate_key", ""),
            "action": action,
            "processed_at": now,
        }
    return sync_state


def remove_backlog_items(backlog, candidate_keys):
    if not candidate_keys:
        return backlog
    items = backlog.setdefault("items", {})
    for key in set(candidate_keys):
        items.pop(key, None)
    return backlog


def format_tv_apply_summary(applied_items, disabled_items, failed_items, processed_sheet_rows=None, sheet_delete_result=None):
    processed_sheet_rows = list(processed_sheet_rows or [])
    sheet_delete_result = sheet_delete_result or {}
    lines = ["📋 数据质量规则处理结果", ""]
    if applied_items:
        lines.append("✅ 已补充规则:")
        for item in applied_items:
            lines.append(f"• {item['country']} / {item['database']} / {item['dest_tbl']}")
        lines.append("")
    if disabled_items:
        lines.append("⏸️ 已关闭自动校验:")
        for item in disabled_items:
            lines.append(f"• {item['country']} / {item['database']} / {item['dest_tbl']}")
        lines.append("")
    if failed_items:
        lines.append("⚠️ 待人工调整:")
        for item in failed_items:
            lines.append(f"• {item['country']} / {item['database']} / {item['dest_tbl']}")
            lines.append(f"  原因: {item.get('reason', '未知原因')}")
        lines.append("")
    if processed_sheet_rows and not sheet_delete_result.get("success"):
        lines.append("🗑️ 请手动删除确认表中的已处理记录:")
        lines.append(f"• 行号: {', '.join(str(row) for row in processed_sheet_rows)}")
        if sheet_delete_result.get("reason"):
            lines.append(f"• 未自动删除原因: {sheet_delete_result['reason']}")
        lines.append("")
    lines.append("请自行查看并按需调整。")
    return "\n".join(lines)
