#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import QUALITY_RULE_FORM_CONFIG
from core.quality_rule_confirmation import (
    get_pending_form_submission_items,
    load_backlog,
    merge_candidates_into_backlog,
    notify_new_candidates_via_tv,
    save_backlog,
    submit_backlog_items_to_form,
)
from core.quality_rule_gap_scanner import SUPPORTED_DATABASES, scan_quality_rule_gaps


def parse_args():
    parser = argparse.ArgumentParser(description="Scan quality-rule gaps, notify via TV, and submit new candidates to Google Form.")
    parser.add_argument("--databases", nargs="*", default=list(SUPPORTED_DATABASES))
    parser.add_argument("--monitor-level", type=int, default=None)
    parser.add_argument("--git-roots", nargs="*", default=None)
    parser.add_argument("--dry-run-form", action="store_true")
    parser.add_argument("--force-form-resubmit", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    results = scan_quality_rule_gaps(
        args.databases,
        monitor_level=args.monitor_level,
        git_roots=args.git_roots or QUALITY_RULE_FORM_CONFIG.get("git_scan_roots"),
    )

    backlog = load_backlog()
    detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    backlog, new_items = merge_candidates_into_backlog(results, backlog=backlog, detected_at=detected_at)

    form_submission_items = get_pending_form_submission_items(
        backlog,
        include_submitted=args.force_form_resubmit,
    )
    form_result = submit_backlog_items_to_form(form_submission_items, dry_run=args.dry_run_form)
    if not form_result.get("skipped"):
        success_keys = {
            row["candidate_key"]
            for row in form_result["results"]
            if row.get("ok")
        }
        for item in form_submission_items:
            if item["candidate_key"] in success_keys:
                item["form_submitted_at"] = detected_at
                item["last_form_payload_signature"] = json.dumps(
                    {
                        "candidate_key": item.get("candidate_key", ""),
                        "country": item.get("country", ""),
                        "database": item.get("database", ""),
                        "tbl": item.get("dest_tbl", ""),
                        "need_apply": "1",
                        "src_sql": item.get("src_sql", ""),
                        "dest_sql": item.get("dest_sql", ""),
                        "human_check": "0",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )

    tv_result = notify_new_candidates_via_tv(
        new_items,
        confirmation_sheet_url=QUALITY_RULE_FORM_CONFIG.get("confirmation_sheet_url", ""),
    )
    if tv_result.get("success"):
        for item in new_items:
            item["notified_at"] = detected_at

    save_backlog(backlog)

    payload = {
        "new_candidates": len(new_items),
        "pending_form_candidates": len(form_submission_items),
        "force_form_resubmit": args.force_form_resubmit,
        "form_result": form_result,
        "tv_result": tv_result,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"新增待确认候选规则: {len(new_items)}")
        print(f"TV 通知: {'成功' if tv_result.get('success') else '失败/跳过'}")
        if form_result.get("skipped"):
            print(f"Google Form: 跳过 ({form_result.get('reason')})")
        else:
            print(f"Google Form: 已提交 {form_result.get('submitted', 0)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
