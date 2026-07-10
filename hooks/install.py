#!/usr/bin/env python3
"""Pill setup — run this once after installing Pill.app:

    python3 "/Applications/Pill.app/Contents/Resources/hooks/install.py"

Copies the hook scripts to ~/.claude/hooks and registers them in
~/.claude/settings.json. Safe to re-run: skips anything already
registered, and backs up settings.json before writing to it.
"""
import json
import os
import shutil
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


def already_registered(existing_entries, command):
    return any(
        h.get("command") == command
        for entry in existing_entries
        for h in entry.get("hooks", [])
    )


def merge_hooks(settings, snippet_hooks):
    settings.setdefault("hooks", {})
    changed = False
    for event, entries in snippet_hooks.items():
        existing = settings["hooks"].setdefault(event, [])
        for entry in entries:
            commands = [h.get("command") for h in entry.get("hooks", [])]
            if not any(already_registered(existing, cmd) for cmd in commands):
                existing.append(entry)
                changed = True
    return changed


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
    print("\nDone. Restart any running Claude Code sessions to pick this up.")


if __name__ == "__main__":
    main()
