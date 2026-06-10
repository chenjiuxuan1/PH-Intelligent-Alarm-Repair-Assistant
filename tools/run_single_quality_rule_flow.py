#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.db_config import get_db_connection
from config.config import QUALITY_RULE_FORM_CONFIG
from core.quality_rule_confirmation import (
    backlog_item_has_submittable_sql,
    build_candidate_key,
    confirmation_row_has_submittable_sql,
    compute_form_payload_signature,
    fetch_confirmation_csv,
    find_latest_confirmation_row,
    find_latest_requested_metric_field,
    load_backlog,
    merge_candidates_into_backlog,
    parse_confirmation_rows,
    save_backlog,
    submit_backlog_items_to_form,
)
from core.quality_rule_gap_scanner import (
    COUNT_RULE_DATABASES,
    EXISTS_RULE_DATABASES,
    build_exists_rule_candidate,
    build_count_rule_candidate,
    default_git_scan_roots,
    load_ods_table_by_dest,
    load_quality_rules,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Run single-table quality rule generation flow.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--tbl", required=True)
    parser.add_argument("--git-root", action="append", dest="git_roots", default=None)
    return parser.parse_args()


def empty_form_result():
    return {"submitted": 0, "results": [], "skipped": True}


def empty_tv_result(reason="deferred_batch_notification"):
    return {"success": True, "skipped": True, "reason": reason}


def emit(full_chain_result, batch_payload):
    print("===FULL_CHAIN_RESULT===")
    print(json.dumps(full_chain_result, ensure_ascii=False, indent=2, default=str))
    print("===LANGFUSE_BATCH===")
    print(json.dumps(batch_payload, ensure_ascii=False))


def load_langfuse_batch():
    export_path = os.environ.get("QUALITY_RULE_LANGFUSE_EXPORT_PATH", "").strip()
    if not export_path:
        return {"batch": []}
    path = Path(export_path)
    if not path.exists():
        return {"batch": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"batch": []}


def build_non_backlog_payload(database, table, result):
    candidate_key = ""
    try:
        candidate_key = build_candidate_key(result)
    except Exception:
        candidate_key = ""
    return {
        "single_table": f"{database}.{table}",
        "candidate_key": candidate_key,
        "scan_result": result,
        "new_candidates": 0,
        "new_candidate_keys": [],
        "form_submission_items": 0,
        "form_result": empty_form_result(),
        "tv_result": empty_tv_result(),
        "backlog_item": {},
    }


def load_confirmation_rows():
    export_url = (QUALITY_RULE_FORM_CONFIG.get("confirmation_export_url") or "").strip()
    if not export_url:
        return []
    try:
        csv_text = fetch_confirmation_csv(export_url)
        return parse_confirmation_rows(csv_text, QUALITY_RULE_FORM_CONFIG.get("confirmation_column_map", {}))
    except Exception:
        return []


def load_single_table(cursor, database, table_name):
    if database in {"ods", "ods_security"}:
        cursor.execute(
            "select * from wattrel_ods_table_settings where dest_db=%s and dest_tbl=%s limit 1",
            (database, table_name),
        )
        return cursor.fetchone(), "wattrel_ods_table_settings"

    cursor.execute(
        "select * from wattrel_etl_table_settings where db=%s and tbl=%s limit 1",
        (database, table_name),
    )
    return cursor.fetchone(), "wattrel_etl_table_settings"


def load_requested_metric_field(rows, database, table_name):
    return find_latest_requested_metric_field(rows, database, table_name)


def main():
    args = parse_args()
    database = args.database
    table_name = args.tbl
    detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    git_roots = args.git_roots or default_git_scan_roots()
    confirmation_rows = load_confirmation_rows()
    target_country = str(QUALITY_RULE_FORM_CONFIG.get("country", "ph")).strip().lower()
    existing_confirmation_row = find_latest_confirmation_row(
        confirmation_rows,
        database,
        table_name,
        country=target_country,
    )
    requested_metric_field = load_requested_metric_field(confirmation_rows, database, table_name)

    if existing_confirmation_row and confirmation_row_has_submittable_sql(existing_confirmation_row):
        result = {
            "country": QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
            "database": database,
            "status": "skipped",
            "rule_name": "",
            "dest_tbl": table_name,
            "dest_db": database,
            "reason": "Google 确认表中已存在该表记录，跳过重复生成",
            "existing_confirmation_row": existing_confirmation_row,
            "validation_status": "not_validated",
            "ai_status": "not_applicable",
        }
        if requested_metric_field:
            result["requested_metric_field"] = requested_metric_field
        emit(build_non_backlog_payload(database, table_name, result), load_langfuse_batch())
        return 0

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        table, config_table_name = load_single_table(cur, database, table_name)
        if not table:
            result = {
                "country": QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
                "database": database,
                "status": "blocked",
                "rule_name": "cnt" if database in COUNT_RULE_DATABASES else "if_exists",
                "dest_tbl": table_name,
                "dest_db": database,
                "reason": f"未在 {config_table_name} 中查到表配置",
                "ai_status": "not_applicable",
                "validation_status": "not_validated",
            }
            emit(build_non_backlog_payload(database, table_name, result), {"batch": []})
            return 0

        rules = load_quality_rules(cur, database)
        if database in COUNT_RULE_DATABASES:
            ods_map = load_ods_table_by_dest(cur)
            raw_result = build_count_rule_candidate(
                database,
                table,
                rules,
                ods_map,
                git_roots=git_roots,
                requested_metric_field=requested_metric_field,
            )
        elif database in EXISTS_RULE_DATABASES:
            raw_result = build_exists_rule_candidate(
                database,
                table,
                rules,
                git_roots=git_roots,
                cursor=cur,
                requested_metric_field=requested_metric_field,
            )
        else:
            result = {
                "country": QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
                "database": database,
                "status": "blocked",
                "rule_name": "",
                "dest_tbl": table_name,
                "dest_db": database,
                "reason": f"不支持的数据库类型: {database}",
                "ai_status": "not_applicable",
                "validation_status": "not_validated",
            }
            emit(build_non_backlog_payload(database, table_name, result), {"batch": []})
            return 0
    finally:
        conn.close()

    result = {
        "country": QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
        "database": database,
        **raw_result,
    }
    if requested_metric_field:
        result["requested_metric_field"] = requested_metric_field
        if result.get("candidate"):
            result["candidate"]["requested_metric_field"] = requested_metric_field
    if result.get("status") in {"existing", "skipped"}:
        emit(build_non_backlog_payload(database, table_name, result), load_langfuse_batch())
        return 0

    backlog = load_backlog()
    backlog, new_items = merge_candidates_into_backlog([result], backlog=backlog, detected_at=detected_at)
    candidate_key = build_candidate_key(result)
    target_item = backlog.get("items", {}).get(candidate_key)
    if not target_item:
        emit(build_non_backlog_payload(database, table_name, result), load_langfuse_batch())
        return 0

    target_item["form_submitted_at"] = None
    target_item["last_form_payload_signature"] = ""
    form_items = [target_item] if backlog_item_has_submittable_sql(target_item) else []
    form_result = submit_backlog_items_to_form(form_items, dry_run=False) if form_items else empty_form_result()
    success_keys = {
        row["candidate_key"]
        for row in form_result.get("results", [])
        if row.get("ok")
    }
    for item in form_items:
        if item["candidate_key"] in success_keys:
            item["form_submitted_at"] = detected_at
            item["last_form_payload_signature"] = compute_form_payload_signature(item)

    save_backlog(backlog)
    payload = {
        "single_table": f"{database}.{table_name}",
        "candidate_key": candidate_key,
        "scan_result": result,
        "new_candidates": len(new_items),
        "new_candidate_keys": [item["candidate_key"] for item in new_items],
        "form_submission_items": len(form_items),
        "form_result": form_result,
        "tv_result": empty_tv_result(),
        "backlog_item": target_item,
    }
    emit(payload, load_langfuse_batch())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
