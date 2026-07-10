#!/usr/bin/env python3
"""Claude Pill — status event hook (fire-and-forget).

Wire this to SessionStart, UserPromptSubmit, Notification, Stop, SessionEnd.
It forwards the event to the widget so the pill can show which sessions
exist, which repo they're in, and whether they're working / idle / blocked.
Never blocks, never prints (UserPromptSubmit stdout would become context!).
"""
import json
import sys
import urllib.request

PILL_URL = "http://127.0.0.1:7777/event"

try:
    data = json.load(sys.stdin)
    payload = json.dumps({
        "hook_event_name": data.get("hook_event_name", ""),
        "session_id": data.get("session_id", ""),
        "cwd": data.get("cwd", ""),
        "message": data.get("message", ""),
        "permission_mode": data.get("permission_mode", ""),
    }).encode()
    req = urllib.request.Request(
        PILL_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=2).read()
except Exception:
    pass
sys.exit(0)
