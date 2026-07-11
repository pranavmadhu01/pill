#!/usr/bin/env python3
"""Self-check for pill_pretooluse.py's permission-rule matcher.
Run directly: python3 hooks/test_pill_pretooluse.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pill_pretooluse import (
    _domain_matches,
    _frontmost_matches_project,
    _path_matches,
    _rule_matches,
    already_settled,
)


def t(desc, cond):
    assert cond, f"FAILED: {desc}"


# Bash command matching
t("bare tool matches any command",
  _rule_matches("Bash", "Bash", {"command": "rm -rf /"}, "/x"))
t("exact match",
  _rule_matches("Bash(npm run build)", "Bash", {"command": "npm run build"}, "/x"))
t("exact non-match",
  not _rule_matches("Bash(npm run build)", "Bash", {"command": "npm run build extra"}, "/x"))
t("prefix with space-star",
  _rule_matches("Bash(git commit *)", "Bash", {"command": "git commit -m x"}, "/x"))
t("prefix with :* shorthand",
  _rule_matches("Bash(ls:*)", "Bash", {"command": "ls -la"}, "/x"))
t("word boundary rejects partial word",
  not _rule_matches("Bash(ls *)", "Bash", {"command": "lsof -i"}, "/x"))
t("middle wildcard",
  _rule_matches("Bash(git * main)", "Bash", {"command": "git push origin main"}, "/x"))
t("wrong tool doesn't match",
  not _rule_matches("Bash(ls *)", "Edit", {"command": "ls"}, "/x"))

# File path matching
t("bare filename matches at any depth",
  _path_matches(".env", "config/.env", "/proj"))
t("bare filename rejects different name at a boundary",
  not _path_matches(".env", "src/myconfig.env", "/proj"))
t("** crosses directories",
  _path_matches("src/**/*.ts", "src/a/b/c.ts", "/proj"))
t("* stays within a segment",
  not _path_matches("src/*.ts", "src/a/c.ts", "/proj"))
t("absolute // pattern",
  _path_matches("//etc/passwd", "/etc/passwd", "/proj"))

# WebFetch domain matching
t("exact domain", _domain_matches("example.com", "example.com"))
t("subdomain wildcard, single label", _domain_matches("*.example.com", "api.example.com"))
t("subdomain wildcard rejects extra label", not _domain_matches("*.example.com", "a.b.example.com"))
t("tld wildcard doesn't cross dots", not _domain_matches("example.*", "example.evil.com"))
t("bare star matches everything", _domain_matches("*", "anything.at.all"))

# Frontmost-window focus check
t("matching app and title skips the widget",
  _frontmost_matches_project("Code", "main.rs — pill", "/Users/user/pill"))
t("right app, wrong project in title",
  not _frontmost_matches_project("Code", "main.rs — some-other-repo", "/Users/user/pill"))
t("right title, non-terminal app doesn't count",
  not _frontmost_matches_project("Safari", "pill", "/Users/user/pill"))
t("iTerm counts as terminal-like",
  _frontmost_matches_project("iTerm2", "~/pill — zsh", "/Users/user/pill"))
t("windows Code.exe (lowercased) counts as terminal-like",
  _frontmost_matches_project("code.exe", "main.rs - pill - Visual Studio Code", "/Users/user/pill"))
t("windows unrelated exe doesn't count",
  not _frontmost_matches_project("chrome.exe", "pill", "/Users/user/pill"))

if sys.platform == "win32":
    from pill_pretooluse import relevant_window_is_frontmost
    t("frontmost check runs without crashing on real Windows",
      isinstance(relevant_window_is_frontmost(os.getcwd()), bool))

# Live permission_mode handling (no settings.json rules involved -- a
# nonexistent cwd/home guarantees an empty rule set)
NOWHERE = "/nonexistent-pill-test-dir"
t("acceptEdits settles Write",
  already_settled("Write", {"file_path": "x.py"}, NOWHERE, "acceptEdits"))
t("acceptEdits settles Edit/MultiEdit/NotebookEdit too",
  all(already_settled(tool, {}, NOWHERE, "acceptEdits") for tool in
      ("Edit", "MultiEdit", "NotebookEdit")))
t("acceptEdits does NOT settle Bash",
  not already_settled("Bash", {"command": "ls"}, NOWHERE, "acceptEdits"))
t("bypassPermissions settles everything",
  already_settled("Bash", {"command": "ls"}, NOWHERE, "bypassPermissions"))
t("auto mode settles everything too",
  already_settled("Bash", {"command": "ls"}, NOWHERE, "auto"))
t("default mode settles nothing on its own",
  not already_settled("Write", {"file_path": "x.py"}, NOWHERE, "default"))
t("plan mode settles nothing either -- exiting plan to run something is a real decision",
  not already_settled("Bash", {"command": "ls"}, NOWHERE, "plan"))

print("all checks passed")
