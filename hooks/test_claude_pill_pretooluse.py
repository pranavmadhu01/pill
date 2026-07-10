#!/usr/bin/env python3
"""Self-check for claude_pill_pretooluse.py's permission-rule matcher.
Run directly: python3 hooks/test_claude_pill_pretooluse.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from claude_pill_pretooluse import _domain_matches, _path_matches, _rule_matches


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

print("all checks passed")
