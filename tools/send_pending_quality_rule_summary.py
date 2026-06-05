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
    load_backlog,
    notify_new_candidates_via_tv,
    save_backlog,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Send one merged TV notification for pending quality-rule confirmations.")
    parser.add_argument(
        "--candidate-key",
        action="append",
        dest="candidate_keys",
        default=None,
        help="Candidate key to include. Repeat this flag for multiple backlog items.",
    )
    parser.add_argument("--json", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def collect_items(backlog, candidate_keys):
    items = backlog.get("items", {})
    selected = []
    for key in candidate_keys or []:
        item = items.get(key)
        if not item:
            continue
        if item.get("status") != "pending_confirmation":
            continue
        selected.append(item)
    return selected


def main():
    args = parse_args()
    candidate_keys = [key.strip() for key in (args.candidate_keys or []) if str(key).strip()]
    backlog = load_backlog()
    pending_items = collect_items(backlog, candidate_keys)
    tv_result = notify_new_candidates_via_tv(
        pending_items,
        confirmation_sheet_url=QUALITY_RULE_FORM_CONFIG.get("confirmation_sheet_url", ""),
    )

    if tv_result.get("success"):
        notified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in pending_items:
            item["notified_at"] = notified_at
        save_backlog(backlog)

    payload = {
        "requested_candidate_keys": candidate_keys,
        "notified_candidates": len(pending_items),
        "notified_candidate_keys": [item.get("candidate_key", "") for item in pending_items],
        "tv_result": tv_result,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
