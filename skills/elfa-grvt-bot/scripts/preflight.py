#!/usr/bin/env python3
"""preflight.py: probe every external dependency once, fail loud if any
credential or endpoint is unhealthy.

Run automatically by bootstrap.py between .env validation and starting
the receiver, so a bad key / geo-block / wrong chat_id surfaces in
~200ms instead of at first fire (which costs a full strategy cycle to
discover).

Can also be run standalone any time:

    set -a && source .env && set +a
    python3 scripts/preflight.py

Exit codes:
    0 - all probes passed
    1 - one or more probes failed (details printed to stdout)

Probes:
    1. Elfa: GET /v2/auto/queries?limit=1 (API-key auth)
    2. GRVT: POST /auth/api_key/login (must return Set-Cookie: gravity=...)
    3. Telegram (only if both TELEGRAM_* set): getMe

The probes are read-only / metadata-only. No strategies are created,
no orders are placed, no Telegram messages are sent.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional, Tuple

import requests


def _result(name: str, ok: bool, detail: str = "") -> Tuple[bool, str]:
    icon = "[ok]" if ok else "[!!]"
    line = f"{icon} {name}"
    if detail:
        line = f"{line}: {detail}"
    print(line, flush=True)
    return ok, line


def probe_elfa() -> Tuple[bool, str]:
    key = os.environ.get("ELFA_API_KEY", "").strip()
    if not key:
        return _result("elfa", False, "ELFA_API_KEY not set")
    try:
        r = requests.get(
            "https://api.elfa.ai/v2/auto/queries",
            params={"limit": 1},
            headers={"x-elfa-api-key": key},
            timeout=10,
        )
    except requests.RequestException as exc:
        return _result("elfa", False, f"network error: {exc}")
    if r.status_code == 401:
        return _result("elfa", False, "401 unauthorized (ELFA_API_KEY rejected)")
    if not r.ok:
        return _result("elfa", False, f"HTTP {r.status_code}: {r.text[:200]}")
    return _result("elfa", True, "API key accepted")


def probe_grvt() -> Tuple[bool, str]:
    key = os.environ.get("GRVT_API_KEY", "").strip()
    if not key:
        return _result("grvt", False, "GRVT_API_KEY not set")
    try:
        r = requests.post(
            "https://edge.grvt.io/auth/api_key/login",
            json={"api_key": key},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return _result("grvt", False, f"network error: {exc}")
    # GRVT's edge returns HTTP 200 with an error body on geo-block or bad
    # key. The canonical signal is whether Set-Cookie: gravity=... was set.
    cookie = r.cookies.get("gravity")
    if cookie:
        return _result("grvt", True, "API key accepted, cookie issued")
    # Surface the actual error body, not the misleading 200.
    try:
        body = r.json()
        msg = body.get("error") or body.get("message") or json.dumps(body)
    except ValueError:
        msg = r.text[:200] or f"HTTP {r.status_code}"
    # Common patterns
    if "location" in msg.lower() or "geo" in msg.lower():
        return _result(
            "grvt", False,
            f"GEO-BLOCK: {msg}  (deploy from an allowed region or VPN)",
        )
    return _result("grvt", False, f"login returned no cookie. body: {msg}")


def probe_telegram() -> Optional[Tuple[bool, str]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token and not chat:
        print("[ok] telegram: skipped (not configured, alerts will be in-chat only)")
        return None
    if not token or not chat:
        return _result(
            "telegram", False,
            "only one of TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID is set; "
            "either set both or leave both blank",
        )
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10,
        )
    except requests.RequestException as exc:
        return _result("telegram", False, f"network error: {exc}")
    if r.status_code == 401:
        return _result("telegram", False, "401 (TELEGRAM_BOT_TOKEN rejected)")
    if not r.ok:
        return _result("telegram", False, f"HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    if not body.get("ok"):
        return _result("telegram", False, f"bot rejected: {body}")
    username = body.get("result", {}).get("username", "<unknown>")
    return _result("telegram", True, f"bot @{username} reachable")


def main() -> int:
    print("preflight: probing external dependencies", flush=True)
    print(flush=True)
    results = [probe_elfa(), probe_grvt()]
    tg = probe_telegram()
    if tg is not None:
        results.append(tg)
    print(flush=True)
    failed = [line for ok, line in results if not ok]
    if failed:
        print("preflight: FAIL", flush=True)
        print("  fix the failed probes above before starting the receiver.", flush=True)
        return 1
    print("preflight: all probes passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
