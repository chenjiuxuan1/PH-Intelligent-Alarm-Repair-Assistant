#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.quality_rule_gap_scanner import list_pending_generation_tables, scan_quality_rule_gaps


def parse_args():
    parser = argparse.ArgumentParser(
        description="List pending quality-rule tables."
    )
    parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        default=None,
        help="Target database. Repeat this flag to scan multiple databases.",
    )
    parser.add_argument("--monitor-level", type=int, default=None)
    parser.add_argument("--git-root", action="append", dest="git_roots", default=None)
    parser.add_argument(
        "--scan-mode",
        choices=("metadata", "evaluated"),
        default="metadata",
        help="metadata: only list tables missing rules; evaluated: run full scanner and keep selected statuses.",
    )
    parser.add_argument(
        "--statuses",
        default="candidate,blocked",
        help="Comma-separated statuses to keep in evaluated mode. Default: candidate,blocked",
    )
    parser.add_argument("--json", action="store_true", help="Pretty-print JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    databases = [db.strip() for db in (args.databases or []) if str(db).strip()]
    if not databases:
        raise SystemExit("--database is required at least once")
    allowed_statuses = {
        status.strip()
        for status in (args.statuses or "").split(",")
        if status.strip()
    } or {"candidate", "blocked"}

    if args.scan_mode == "metadata":
        items = list_pending_generation_tables(
            databases=databases,
            monitor_level=args.monitor_level,
        )
    else:
        results = scan_quality_rule_gaps(
            databases=databases,
            monitor_level=args.monitor_level,
            git_roots=args.git_roots,
        )

        items = []
        for item in results:
            status = item.get("status", "")
            if status not in allowed_statuses:
                continue
            items.append(
                {
                    "database": item.get("database") or item.get("dest_db") or "",
                    "tbl": item.get("dest_tbl", ""),
                    "status": status,
                    "reason": item.get("reason", ""),
                    "rule_name": item.get("rule_name", ""),
                    "ai_status": item.get("ai_status", ""),
                    "validation_status": item.get("validation_status", ""),
                }
            )

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(items, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
