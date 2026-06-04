#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import QUALITY_RULE_FORM_CONFIG
from core.quality_rule_confirmation import (
    format_tv_apply_summary,
    fetch_confirmation_csv,
    load_backlog,
    parse_confirmation_rows,
    save_backlog,
    update_backlog_with_decisions,
)
from core.quality_rule_gap_scanner import apply_candidates, disable_auto_check_for_items, validate_candidates


def parse_args():
    parser = argparse.ArgumentParser(description="Read Google Form/Sheet confirmations and apply approved quality rules.")
    parser.add_argument("--export-url", default=QUALITY_RULE_FORM_CONFIG.get("confirmation_export_url", ""))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.export_url:
        print("未配置 QUALITY_RULE_CONFIRMATION_EXPORT_URL")
        return 1

    csv_text = fetch_confirmation_csv(args.export_url)
    decision_rows = parse_confirmation_rows(csv_text, QUALITY_RULE_FORM_CONFIG.get("confirmation_column_map", {}))

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

    validation = validate_candidates(candidate_payload)
    validated_keys = {
        row["candidate"].get("candidate_key") or row.get("candidate_key")
        for row in validation["passed"]
    }
    apply_payload = [
        item for item in candidate_payload
        if item["candidate_key"] in validated_keys
    ]
    validation_failed = []
    failed_lookup = {
        (row["candidate"].get("candidate_key") or row.get("candidate_key")): row["reason"]
        for row in validation["failed"]
    }
    for item in approved_items:
        if item["candidate_key"] in failed_lookup:
            item["status"] = "validation_failed"
            item["decision_notes"] = failed_lookup[item["candidate_key"]]
            validation_failed.append({"country": item["country"], "database": item["database"], "dest_tbl": item["dest_tbl"], "reason": failed_lookup[item["candidate_key"]]})

    applied_count = apply_candidates(apply_payload)
    if applied_count:
        from datetime import datetime

        applied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in approved_items:
            if item.get("status") == "approved" and not item.get("applied_at") and item["candidate_key"] in {row["candidate_key"] for row in apply_payload}:
                item["status"] = "applied"
                item["applied_at"] = applied_at

    rejected_pending = [
        item for item in rejected_items
        if item.get("status") == "rejected" and not item.get("applied_at")
    ]
    disabled_count = disable_auto_check_for_items(rejected_pending)
    if disabled_count:
        from datetime import datetime

        disabled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in rejected_pending:
            item["status"] = "disabled_auto_check"
            item["applied_at"] = disabled_at

    save_backlog(backlog)

    applied_items = [item for item in approved_items if item.get("status") == "applied"]
    disabled_items = [item for item in rejected_pending if item.get("status") == "disabled_auto_check"]
    tv_result = {"success": True, "skipped": True}
    summary_message = format_tv_apply_summary(applied_items, disabled_items, validation_failed)
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
