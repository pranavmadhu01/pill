#!/usr/bin/env python3
"""Claude Pill — PreToolUse hook.

Sends the pending tool call to the Claude Pill widget and BLOCKS until the
user taps Allow / Deny / Terminal there.

But only when Claude Code would actually have to ask: if the call is already
covered by an existing allow/deny rule in settings.json, we skip the widget
entirely and let Claude Code's normal permission check handle it silently,
exactly as if this hook didn't exist.

Fallback behavior (important): if the widget isn't running, errors out, or
times out, this script exits 0 with no output — Claude Code then continues
with its normal permission flow in the terminal. The widget can never lock
you out of a session.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request

PILL_URL = "http://127.0.0.1:7777/approval"
# Must be >= the widget's APPROVAL_WAIT_SECS (280) and < the hook "timeout"
# configured in settings.json (300).
REQUEST_TIMEOUT = 290

# Apps that plausibly host the terminal driving this Claude Code session.
TERMINAL_LIKE_APPS = {"Visual Studio Code", "Code", "Cursor", "Terminal", "iTerm2", "iTerm"}


def summarize(tool: str, tool_input: dict) -> str:
    if tool == "Bash":
        return tool_input.get("command", "")
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return tool_input.get("file_path", "")
    if tool == "WebFetch":
        return tool_input.get("url", "")
    return json.dumps(tool_input)[:300]


# ---------------------------------------------------------- permission rules
#
# Replicates just enough of Claude Code's settings.json permission matching
# (see docs on the `permissions` key) to answer one question: would Claude
# Code prompt for this on its own, or has the user already settled it via an
# allow/deny rule? We only need to get "already settled" right — anything we
# misjudge just falls through to the widget, which is the safe direction.

def _load_json(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _load_permission_rules(cwd: str):
    paths = [
        os.path.expanduser("~/.claude/settings.json"),
        os.path.join(cwd, ".claude", "settings.json"),
        os.path.join(cwd, ".claude", "settings.local.json"),
    ]
    allow, deny, ask = [], [], []
    bypass = False
    for path in paths:
        perms = _load_json(path).get("permissions", {}) or {}
        allow += perms.get("allow", []) or []
        deny += perms.get("deny", []) or []
        ask += perms.get("ask", []) or []
        if perms.get("defaultMode") == "bypassPermissions":
            bypass = True
    return allow, deny, ask, bypass


def _domain_matches(pattern: str, host: str) -> bool:
    if pattern == "*":
        return True
    p_labels, h_labels = pattern.split("."), host.split(".")
    if len(p_labels) != len(h_labels):
        return False
    return all(p == "*" or p.lower() == h.lower() for p, h in zip(p_labels, h_labels))


def _glob_to_regex(pattern: str) -> str:
    # gitignore-style: "**" crosses path separators, "*"/"?" stay within a
    # single segment, everything else is literal.
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*" and i + 1 < n and pattern[i + 1] == "*":
            i += 2
            if i < n and pattern[i] == "/":
                i += 1
                out.append("(?:.*/)?")  # "**/" -- optional path prefix, dir-boundary safe
            else:
                out.append(".*")  # bare "**" not followed by "/" -- match anything
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "^" + "".join(out) + "$"


def _path_matches(pattern: str, file_path: str, cwd: str) -> bool:
    if not file_path:
        return False
    abs_path = os.path.abspath(os.path.join(cwd, file_path))
    if pattern.startswith("//"):
        candidate, glob_pattern = abs_path, pattern[1:]
    elif pattern.startswith("~/"):
        candidate, glob_pattern = abs_path, os.path.expanduser(pattern)
    elif "/" not in pattern:
        candidate = os.path.relpath(abs_path, cwd)
        glob_pattern = "**/" + pattern
    else:
        candidate = os.path.relpath(abs_path, cwd)
        glob_pattern = pattern[2:] if pattern.startswith("./") else pattern
    return re.match(_glob_to_regex(glob_pattern), candidate) is not None


_RULE_RE = re.compile(r"^([A-Za-z]+)(?:\((.*)\))?$")


def _rule_matches(rule: str, tool: str, tool_input: dict, cwd: str) -> bool:
    m = _RULE_RE.match(rule.strip())
    if not m or m.group(1) != tool:
        return False
    spec = m.group(2)
    if not spec or spec == "*":
        return True

    if tool == "Bash":
        command = tool_input.get("command", "")
        if spec.endswith(":*"):
            spec = spec[:-2] + " *"
        return fnmatch.fnmatchcase(command, spec)

    if tool == "WebFetch":
        if not spec.startswith("domain:"):
            return False
        url = tool_input.get("url", "")
        host = re.sub(r"^\w+://", "", url).split("/")[0].split(":")[0]
        return _domain_matches(spec[len("domain:"):], host)

    # Write, Edit, MultiEdit, NotebookEdit -- all key off file_path
    return _path_matches(spec, tool_input.get("file_path", ""), cwd)


EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def already_settled(tool: str, tool_input: dict, cwd: str, permission_mode: str) -> bool:
    """True if Claude Code's own settings.json rules (or its live permission
    mode -- "default"/"acceptEdits"/"auto"/"bypassPermissions"/"plan",
    toggled at runtime and not reflected in settings.json at all) already
    decide this one way or the other, with no interactive prompt involved
    -- so the widget has nothing to add. False means Claude Code would
    ask, which is exactly the case the widget exists for."""
    try:
        allow, deny, ask, bypass = _load_permission_rules(cwd)
        if any(_rule_matches(r, tool, tool_input, cwd) for r in deny):
            return True  # Claude Code refuses outright, no prompt either way
        if any(_rule_matches(r, tool, tool_input, cwd) for r in ask):
            return False  # explicit ask rule -- a real prompt is coming
        if any(_rule_matches(r, tool, tool_input, cwd) for r in allow):
            return True
        if permission_mode in ("bypassPermissions", "auto") or bypass:
            return True
        if permission_mode == "acceptEdits" and tool in EDIT_TOOLS:
            return True  # auto-accept edits mode -- file edits don't prompt
        return False  # nothing settled it: default mode asks
    except Exception:
        return False  # unsure -> let the widget handle it, the safe direction


# ------------------------------------------------------------ focus check
#
# If the user is already looking at the window driving this session, the
# pill just adds a click they don't need -- let Claude Code's normal
# terminal prompt handle it instead. Any failure here (no permission
# granted for System Events yet, osascript missing, etc.) just falls
# through to showing the widget, the safe direction.

def _frontmost_matches_project(app_name: str, window_title: str, cwd: str) -> bool:
    if app_name not in TERMINAL_LIKE_APPS:
        return False
    project = os.path.basename(cwd.rstrip("/"))
    return bool(project) and project.lower() in window_title.lower()


def relevant_window_is_frontmost(cwd: str) -> bool:
    script = (
        'tell application "System Events"\n'
        "  set frontProc to first application process whose frontmost is true\n"
        "  set appName to name of frontProc\n"
        '  set winTitle to ""\n'
        "  try\n"
        "    set winTitle to name of front window of frontProc\n"
        "  end try\n"
        '  return appName & "|||" & winTitle\n'
        "end tell"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return False
        app_name, _, window_title = result.stdout.strip().partition("|||")
        return _frontmost_matches_project(app_name, window_title, cwd)
    except Exception:
        return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        with open(os.path.expanduser("~/claude_pill_payload_debug.json"), "a") as f:
            f.write(json.dumps(data, indent=2) + "\n---\n")
    except Exception:
        pass

    tool = data.get("tool_name", "Tool")
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd", "") or os.getcwd()
    permission_mode = data.get("permission_mode", "default")

    if already_settled(tool, tool_input, cwd, permission_mode):
        sys.exit(0)  # already covered by a rule or the live permission mode

    if relevant_window_is_frontmost(cwd):
        sys.exit(0)  # already looking at the right window -- just use the terminal

    payload = json.dumps({
        "session_id": data.get("session_id", ""),
        "cwd": cwd,
        "tool_name": tool,
        "summary": summarize(tool, tool_input)[:600],
        "detail": json.dumps(tool_input, indent=2)[:2000],
        "permission_mode": permission_mode,
    }).encode()

    try:
        req = urllib.request.Request(
            PILL_URL, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            answer = json.load(resp)
    except Exception:
        sys.exit(0)  # widget not running -> normal terminal prompt

    decision = answer.get("decision", "passthrough")
    if decision not in ("allow", "deny"):
        sys.exit(0)  # "passthrough" / unknown -> normal terminal prompt

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": answer.get("reason", "Decided in Claude Pill"),
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
