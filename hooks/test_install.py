#!/usr/bin/env python3
"""Self-check for install.py's settings.json merge logic.
Run directly: python3 hooks/test_install.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from install import already_registered, merge_hooks


def t(desc, cond):
    assert cond, f"FAILED: {desc}"


existing = [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/pill_event.py"}]}]
t("finds an already-registered command", already_registered(existing, "python3 ~/.claude/hooks/pill_event.py"))
t("doesn't match a different command", not already_registered(existing, "python3 ~/.claude/hooks/other.py"))
t("empty entries never match", not already_registered([], "anything"))

# Fresh settings, nothing registered yet -- everything should get added
snippet_hooks = {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 pretooluse.py"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 event.py"}]}],
}
settings = {}
changed = merge_hooks(settings, snippet_hooks)
t("reports a change on first merge", changed is True)
t("PreToolUse got registered", settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "python3 pretooluse.py")
t("SessionStart got registered", settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "python3 event.py")

# Re-running against the same settings should be a no-op
changed_again = merge_hooks(settings, snippet_hooks)
t("re-running is idempotent (no change)", changed_again is False)
t("re-running doesn't duplicate entries", len(settings["hooks"]["PreToolUse"]) == 1)

# An existing, unrelated hook for the same event must survive the merge
settings_with_other = {
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "python3 some-other-tool.py"}]}]}
}
merge_hooks(settings_with_other, snippet_hooks)
commands = [h["command"] for e in settings_with_other["hooks"]["SessionStart"] for h in e["hooks"]]
t("existing unrelated hook is preserved", "python3 some-other-tool.py" in commands)
t("our hook is appended alongside it", "python3 event.py" in commands)

print("all checks passed")
