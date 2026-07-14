#!/usr/bin/env python3
"""Self-check for install.py's settings.json merge logic.
Run directly: python3 hooks/test_install.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from install import _build_command, _hook_basename, already_registered, merge_hooks


def t(desc, cond):
    assert cond, f"FAILED: {desc}"


# Basename extraction -- what dedup keys off, regardless of interpreter or
# path separator style
t("extracts filename from a posix command", _hook_basename("python3 ~/.claude/hooks/pill_event.py") == "pill_event.py")
t("extracts filename from a windows command", _hook_basename('"C:\\Python312\\python.exe" "C:\\Users\\x\\.claude\\hooks\\pill_event.py"') == "pill_event.py")
t("empty command has no basename", _hook_basename("") == "")

# Command building -- always uses the running interpreter, never a
# hardcoded "python3"
built = _build_command("pill_event.py")
t("built command references sys.executable", sys.executable in built)
t("built command references the hook file", "pill_event.py" in built)

existing = [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/pill_event.py"}]}]
t("finds an already-registered hook by filename", already_registered(existing, "pill_event.py"))
t("doesn't match a different filename", not already_registered(existing, "other.py"))
t("empty entries never match", not already_registered([], "anything"))

# Fresh settings, nothing registered yet -- everything should get added
snippet_hooks = {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 pill_pretooluse.py"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 pill_event.py"}]}],
}
settings = {}
changed = merge_hooks(settings, snippet_hooks)
t("reports a change on first merge", changed is True)
t("PreToolUse got registered", "pill_pretooluse.py" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
t("SessionStart got registered", "pill_event.py" in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"])
t("matcher survives the rebuild", settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash")

# Re-running against the same settings should be a no-op, even though the
# rebuilt command string won't byte-for-byte match what's already there
changed_again = merge_hooks(settings, snippet_hooks)
t("re-running is idempotent (no change)", changed_again is False)
t("re-running doesn't duplicate entries", len(settings["hooks"]["PreToolUse"]) == 1)

# An existing, unrelated hook for the same event must survive the merge
settings_with_other = {
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "python3 some-other-tool.py"}]}]}
}
merge_hooks(settings_with_other, snippet_hooks)
commands = [h["command"] for e in settings_with_other["hooks"]["SessionStart"] for h in e["hooks"]]
t("existing unrelated hook is preserved", any("some-other-tool.py" in c for c in commands))
t("our hook is appended alongside it", any("pill_event.py" in c for c in commands))

print("all checks passed")
