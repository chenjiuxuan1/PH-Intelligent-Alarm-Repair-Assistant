#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        print("usage: replay_langfuse_trace.py <batch-json-path>", file=sys.stderr)
        return 2

    batch_path = Path(sys.argv[1])
    if not batch_path.exists():
        print(f"batch file not found: {batch_path}", file=sys.stderr)
        return 2

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    base_url = os.environ.get("LANGFUSE_BASE_URL", "").strip().rstrip("/")
    if not public_key or not secret_key or not base_url:
        print("missing LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL", file=sys.stderr)
        return 2

    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{base_url}/api/public/ingestion",
        data=json.dumps(batch, ensure_ascii=True).encode("utf-8"),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "User-Agent": "PH-Quality-Rule-AI-Replay/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(json.dumps({"ok": True, "status": resp.getcode(), "body": body}, ensure_ascii=False, indent=2))
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(json.dumps({"ok": False, "status": exc.code, "body": body}, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
