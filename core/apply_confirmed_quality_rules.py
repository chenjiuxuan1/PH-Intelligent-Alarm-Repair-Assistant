#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import QUALITY_RULE_FORM_CONFIG
from core.quality_rule_confirmation import (
    delete_confirmation_sheet_rows,
    extract_sheet_row_number,
    filter_unprocessed_decision_rows,
    format_tv_apply_summary,
    fetch_confirmation_csv,
    load_backlog,
    load_sync_state,
    mark_processed_decisions,
    parse_confirmation_rows,
    remove_backlog_items,
    save_backlog,
    save_sync_state,
    update_backlog_with_decisions,
)
from core.quality_rule_gap_scanner import apply_candidates, disable_auto_check_for_items, validate_candidates_for_apply


def parse_args():
    parser = argparse.ArgumentParser(description="Read Google Form/Sheet confirmations and apply approved quality rules.")
    parser.add_argument("--export-url", default=QUALITY_RULE_FORM_CONFIG.get("confirmation_export_url", ""))
    parser.add_argument("--csv-file", default="", help="Read confirmation rows from a local CSV file instead of Google export URL.")
    parser.add_argument(
        "--decision-json-base64",
        default="",
        help="Base64-encoded JSON array of confirmation rows. Preferred for n8n Google Sheets connector integration.",
    )
    parser.add_argument(
        "--validate-syntax",
        action="store_true",
        help="Validate approved SQL before apply. Disabled by default for human-confirmed apply flows.",
    )
    parser.add_argument(
        "--country",
        default=QUALITY_RULE_FORM_CONFIG.get("country", "ph"),
        help="Only process confirmation rows for this country.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    target_country = (args.country or QUALITY_RULE_FORM_CONFIG.get("country", "ph")).strip().lower()
    if not args.decision_json_base64 and not args.csv_file and not args.export_url:
        print("未配置 QUALITY_RULE_CONFIRMATION_EXPORT_URL")
        return 1

    if args.decision_json_base64:
        decoded = base64.b64decode(args.decision_json_base64.encode("utf-8")).decode("utf-8")
        decision_rows = json.loads(decoded)
        if not isinstance(decision_rows, list):
            raise ValueError("decision-json-base64 解码后必须是 JSON 数组")
    elif args.csv_file:
        csv_text = Path(args.csv_file).read_text(encoding="utf-8")
        decision_rows = parse_confirmation_rows(csv_text, QUALITY_RULE_FORM_CONFIG.get("confirmation_column_map", {}))
    else:
        csv_text = fetch_confirmation_csv(args.export_url)
        decision_rows = parse_confirmation_rows(csv_text, QUALITY_RULE_FORM_CONFIG.get("confirmation_column_map", {}))

    sync_state = load_sync_state()
    decision_rows = filter_unprocessed_decision_rows(decision_rows, sync_state)
    decision_rows = [
        row for row in decision_rows
        if str(row.get("country") or QUALITY_RULE_FORM_CONFIG.get("country", "ph")).strip().lower() == target_country
    ]
    backlog = load_backlog()
    approved_items, rejected_items = update_backlog_with_decisions(backlog, decision_rows)
    candidate_payload = [
        {
            "status": "candidate",
            "candidate_key": item["candidate_key"],
            "database": item["database"],
            "dest_db": item["dest_db"],
            "dest_tbl": item["dest_tbl"],
            "candidate": {
                "name": item["rule_name"],
                "desc": "总数" if item["rule_name"] == "cnt" else "是否存在",
                "src_db": item["src_db"],
                "src_tbl": item["src_tbl"],
                "dest_db": item["dest_db"],
                "dest_tbl": item["dest_tbl"],
                "src_sql": item.get("decision_src_sql") or item["src_sql"],
                "dest_sql": item.get("decision_dest_sql") or item["dest_sql"],
                "msg_template": (
                    "{dest_tbl} 数量不一致  期望值 {src_value}  实际值{dest_value}  差值为 {diff}"
                    if item["rule_name"] == "cnt"
                    else "{dest_tbl} 昨日缺失数据"
                ),
            },
        }
        for item in approved_items
        if item.get("status") == "approved" and not item.get("applied_at")
    ]

    validation_failed = []
    apply_payload = candidate_payload
    if args.validate_syntax:
        validation = validate_candidates_for_apply(candidate_payload)
        validated_keys = {
            row["candidate"].get("candidate_key") or row.get("candidate_key")
            for row in validation["passed"]
        }
        apply_payload = [
            item for item in candidate_payload
            if item["candidate_key"] in validated_keys
        ]
        failed_lookup = {
            (row["candidate"].get("candidate_key") or row.get("candidate_key")): row["reason"]
            for row in validation["failed"]
        }
        for item in approved_items:
            if item["candidate_key"] in failed_lookup:
                item["status"] = "validation_failed"
                item["decision_notes"] = failed_lookup[item["candidate_key"]]
                validation_failed.append(
                    {
                        "country": item["country"],
                        "database": item["database"],
                        "dest_tbl": item["dest_tbl"],
                        "reason": failed_lookup[item["candidate_key"]],
                    }
                )

    applied_count = apply_candidates(apply_payload)
    successfully_applied_items = []
    if applied_count:
        from datetime import datetime

        applied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in approved_items:
            if item.get("status") == "approved" and not item.get("applied_at") and item["candidate_key"] in {row["candidate_key"] for row in apply_payload}:
                item["status"] = "applied"
                item["applied_at"] = applied_at
                successfully_applied_items.append(item)

    rejected_pending = [
        item for item in rejected_items
        if item.get("status") == "rejected" and not item.get("applied_at")
    ]
    disabled_count = disable_auto_check_for_items(rejected_pending)
    successfully_disabled_items = []
    if disabled_count:
        from datetime import datetime

        disabled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in rejected_pending:
            item["status"] = "disabled_auto_check"
            item["applied_at"] = disabled_at
            successfully_disabled_items.append(item)

    processed_items = successfully_applied_items + successfully_disabled_items
    if processed_items:
        sync_state = mark_processed_decisions(sync_state, successfully_applied_items, action="applied")
        sync_state = mark_processed_decisions(sync_state, successfully_disabled_items, action="disabled_auto_check")
        save_sync_state(sync_state)
        remove_backlog_items(backlog, [item["candidate_key"] for item in processed_items])

    save_backlog(backlog)

    applied_items = [item for item in approved_items if item.get("status") == "applied"]
    disabled_items = [item for item in rejected_pending if item.get("status") == "disabled_auto_check"]
    applied_sheet_rows = sorted(
        {
            row_number
            for row_number in (extract_sheet_row_number(item) for item in applied_items)
            if row_number is not None
        },
        reverse=True,
    )
    disabled_sheet_rows = sorted(
        {
            row_number
            for row_number in (extract_sheet_row_number(item) for item in disabled_items)
            if row_number is not None
        },
        reverse=True,
    )
    processed_sheet_rows = sorted(set(applied_sheet_rows + disabled_sheet_rows), reverse=True)
    sheet_delete_result = delete_confirmation_sheet_rows(processed_sheet_rows)
    tv_result = {"success": True, "skipped": True}
    summary_message = format_tv_apply_summary(
        applied_items,
        disabled_items,
        validation_failed,
        processed_sheet_rows=processed_sheet_rows,
        sheet_delete_result=sheet_delete_result,
    )
    if applied_items or disabled_items or validation_failed:
        from core.send_tv_report import send_tv_report
        tv_result = send_tv_report(
            summary_message,
            mentions=QUALITY_RULE_FORM_CONFIG.get("notify_mentions", []),
            bot_id=QUALITY_RULE_FORM_CONFIG.get("notify_bot_id"),
        )

    payload = {
        "approved_candidates": len(approved_items),
        "rejected_candidates": len(rejected_items),
        "applied_count": applied_count,
        "disabled_count": disabled_count,
        "validation_failed_count": len(validation_failed),
        "applied_sheet_rows": applied_sheet_rows,
        "disabled_sheet_rows": disabled_sheet_rows,
        "processed_sheet_rows": processed_sheet_rows,
        "sheet_delete_result": sheet_delete_result,
        "processed_sheet_actions": [
            {
                "candidate_key": item.get("candidate_key", ""),
                "database": item.get("database", ""),
                "dest_tbl": item.get("dest_tbl", ""),
                "action": "applied",
                "sheet_row_number": extract_sheet_row_number(item),
            }
            for item in applied_items
            if extract_sheet_row_number(item) is not None
        ] + [
            {
                "candidate_key": item.get("candidate_key", ""),
                "database": item.get("database", ""),
                "dest_tbl": item.get("dest_tbl", ""),
                "action": "disabled_auto_check",
                "sheet_row_number": extract_sheet_row_number(item),
            }
            for item in disabled_items
            if extract_sheet_row_number(item) is not None
        ],
        "tv_result": tv_result,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"读取确认记录: {len(decision_rows)} 条")
        print(f"批准候选规则: {len(approved_items)} 条")
        print(f"实际写入规则: {applied_count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
