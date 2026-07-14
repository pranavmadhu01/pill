#!/usr/bin/env python3
"""Pill setup — run this once after installing Pill (see README):

    python3 "/Applications/Pill.app/Contents/Resources/hooks/install.py"   (macOS)
    python "<install dir>\\resources\\hooks\\install.py"                    (Windows)

Copies the hook scripts to ~/.claude/hooks and registers them in
~/.claude/settings.json. Safe to re-run: skips anything already
registered, and backs up settings.json before writing to it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.expanduser("~/.claude/hooks")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
HOOK_FILES = ["pill_pretooluse.py", "pill_event.py"]


def install_hook_files():
    os.makedirs(HOOKS_DIR, exist_ok=True)
    for name in HOOK_FILES:
        dst = os.path.join(HOOKS_DIR, name)
        shutil.copyfile(os.path.join(HERE, name), dst)
        print(f"  copied {name} -> {dst}")


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f) or {}


def _quote(s: str) -> str:
    # Hook commands are handed to a shell by Claude Code -- quote for
    # whichever one actually runs them rather than relying on `~` expansion
    # or leaving a path with spaces unquoted, neither of which is a safe bet
    # on Windows.
    if os.name == "nt":
        return f'"{s}"' if " " in s else s
    return shlex.quote(s)


def _hook_basename(command: str) -> str:
    if not command:
        return ""
    last_token = command.replace("\\", "/").split()[-1].strip('"')
    return os.path.basename(last_token)


def _build_command(filename: str) -> str:
    # sys.executable is whatever interpreter is running this script right
    # now -- always resolvable, unlike hardcoding "python3" (missing by
    # that name on a typical Windows install) or trusting `~` to expand.
    interpreter = sys.executable or ("python" if os.name == "nt" else "python3")
    hook_path = os.path.join(HOOKS_DIR, filename)
    return f"{_quote(interpreter)} {_quote(hook_path)}"


def already_registered(existing_entries, filename):
    return any(
        _hook_basename(h.get("command")) == filename
        for entry in existing_entries
        for h in entry.get("hooks", [])
    )


def _repair_stale_commands(existing_entries, filename):
    # A matching filename can already be registered with a stale command --
    # wrong interpreter after a Python upgrade, or a hardcoded python3/~
    # that never resolves on this OS. Rewrite it in place instead of just
    # skipping, so re-running install.py actually fixes a broken hook.
    expected = _build_command(filename)
    changed = False
    for entry in existing_entries:
        for h in entry.get("hooks", []):
            if _hook_basename(h.get("command")) == filename and h.get("command") != expected:
                h["command"] = expected
                changed = True
    return changed


def merge_hooks(settings, snippet_hooks):
    settings.setdefault("hooks", {})
    changed = False
    for event, entries in snippet_hooks.items():
        existing = settings["hooks"].setdefault(event, [])
        for entry in entries:
            filenames = [_hook_basename(h.get("command")) for h in entry.get("hooks", [])]
            if any(already_registered(existing, name) for name in filenames):
                for name in filenames:
                    if _repair_stale_commands(existing, name):
                        changed = True
                continue
            new_entry = dict(entry)
            new_entry["hooks"] = [
                {**h, "command": _build_command(_hook_basename(h.get("command")))}
                for h in entry.get("hooks", [])
            ]
            existing.append(new_entry)
            changed = True
    return changed


def _verify_hook_launches(command: str):
    # Empty stdin makes both hooks hit their top-level except and exit(0)
    # immediately -- no network call, no widget popup, just a pure check
    # that the interpreter and hook path in this command actually resolve
    # on this machine. Hook *logic* is covered by test_pill_pretooluse.py.
    try:
        proc = subprocess.run(
            shlex.split(command, posix=(os.name != "nt")),
            input="", capture_output=True, text=True, timeout=10,
        )
    except OSError as e:
        return f"couldn't launch ({e})"
    except subprocess.TimeoutExpired:
        return "didn't exit within 10s"
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "(no stderr)"
        return f"exited {proc.returncode}: {detail}"
    return None


def verify_installed_hooks():
    problems = []
    for name in HOOK_FILES:
        err = _verify_hook_launches(_build_command(name))
        if err:
            problems.append(f"{name}: {err}")
    return problems


def main():
    print("Setting up Pill's Claude Code hooks...")
    install_hook_files()

    snippet = load_json(os.path.join(HERE, "settings.snippet.json"))
    settings = load_json(SETTINGS_PATH)

    if os.path.exists(SETTINGS_PATH):
        backup = f"{SETTINGS_PATH}.bak-{int(time.time())}"
        shutil.copyfile(SETTINGS_PATH, backup)
        print(f"  backed up existing settings.json -> {backup}")

    changed = merge_hooks(settings, snippet.get("hooks", {}))
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(
        "  merged hook registration into ~/.claude/settings.json"
        if changed
        else "  hooks were already registered, nothing to merge"
    )

    problems = verify_installed_hooks()
    if problems:
        print("\nWARNING: these hooks may not run:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("  verified: both hooks launch cleanly with this interpreter")

    print("\nDone. Restart any running Claude Code sessions to pick this up.")


if __name__ == "__main__":
    main()
