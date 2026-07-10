# Claude Pill

A macOS menu bar utility that shows what your Claude Code sessions are doing
and lets you **Allow / Deny a pending tool call right from the menu bar** —
no tabbing back to the terminal, and no custom floating window to fight with
Spaces or full-screen apps.

```
                                    🔵 status
  ┌──────────────────────────────────────┐
  │ Bash · rm -rf node_modules && npm i  │   ← disabled header, just context
  │ Allow                                │
  │ Deny                                 │
  │ Answer in Terminal                   │   ← also focuses the right VS Code window
  │ ─────────────────────────────────────│
  │ other-repo — working    [auto]       │   ← click = open in VS Code
  └──────────────────────────────────────┘
```

It's a plain `NSStatusItem` + native `NSMenu` — deliberately not a custom
window. An earlier version was a floating always-on-top panel, which turned
into a long fight with macOS Spaces, full-screen apps, and window transparency
that never fully worked. The tray icon + native menu sidesteps all of it for
free, the same way Wi-Fi/Bluetooth/Docker's menu bar items do.

## How it works

- The app runs a local HTTP server on `127.0.0.1:7777`.
- Claude Code **hooks** (configured in `~/.claude/settings.json`, see Setup)
  talk to it:
  - `PreToolUse` → POSTs the pending tool call to `/approval` and **blocks**.
    Before ever contacting the widget, the hook (`hooks/claude_pill_pretooluse.py`)
    checks three things, each of which can skip the widget entirely and let
    Claude Code's own flow handle it silently:
    1. **Does an existing `permissions.allow`/`deny`/`ask` rule in settings.json
       already cover this call?** (replicates Claude Code's own rule matching —
       Bash prefix/wildcard patterns, gitignore-style file globs, domain
       wildcards for WebFetch)
    2. **Is the live session permission mode** (`default` / `acceptEdits` /
       `auto` / `bypassPermissions` / `plan`, toggled via Shift+Tab and *not*
       reflected in settings.json at all) **already permissive enough?**
    3. **Are you already looking at the right window?** — if the frontmost
       app is VS Code/Cursor/Terminal/iTerm *and* its window title matches
       the project, there's no point routing through the menu; let the
       terminal prompt handle it.

    If none of those settle it, the request goes to the widget, which shows
    Allow / Deny / Answer in Terminal in the tray menu and fires a macOS
    notification (re-announced every ~12s until resolved, since banners
    auto-dismiss in a couple seconds — clicking one pops the menu open).
    The hook returns the decision to Claude Code (`permissionDecision:
    allow|deny`). Answering in the terminal (or a timeout, or the widget not
    running) makes the hook exit silently, so Claude Code falls back to its
    normal terminal prompt — the widget can never lock you out.
  - `SessionStart / UserPromptSubmit / Stop / Notification / SessionEnd` →
    fire-and-forget POSTs to `/event` (`hooks/claude_pill_event.py`), which is
    how the menu knows each session's repo, status (working / idle / needs
    attention), and current permission mode.
- The tray icon itself pulses an orange glow while an approval is pending
  (plain icon otherwise), and each session row shows its permission mode as a
  small colored pill-shaped chip.
- "Open in VS Code" / "Answer in Terminal" hit VS Code's own CLI script
  directly (`.../Visual Studio Code.app/Contents/Resources/app/bin/code`, so
  it works without `code` on `PATH`) rather than `open -a`, because `open -a`
  goes through macOS's generic document-open path and skips VS Code's
  "already open somewhere? focus that window" logic — `open -a` was
  reliably opening duplicate windows instead of reusing the existing one.

## Setup

**1. Prerequisites:** Rust (`rustup`), Node.js, Xcode Command Line Tools.

**2. Install dependencies**
```bash
cd claude-pill
npm install
```

**3. Symlink the hooks** (not a copy — this keeps edits to the scripts live
without needing to redeploy every time):
```bash
mkdir -p ~/.claude/hooks
ln -s "$(pwd)/hooks/claude_pill_pretooluse.py" ~/.claude/hooks/claude_pill_pretooluse.py
ln -s "$(pwd)/hooks/claude_pill_event.py" ~/.claude/hooks/claude_pill_event.py
```
Then merge `hooks/settings.snippet.json` into `~/.claude/settings.json`
(create it if it doesn't exist). If you already have hooks configured, append
these entries to the matching arrays rather than replacing them. Unlike the
`.py` scripts, this JSON merge is **not** kept in sync automatically — if you
ever change `settings.snippet.json` (new hook event, different matcher),
re-merge it by hand.

**4. Run it as a real app bundle, not `tauri dev`.** Notifications need a
genuine `.app` bundle to post under their own identity — an unbundled dev
binary can't (legacy `NSUserNotificationCenter` silently no-ops for
impersonated/unregistered bundle IDs). `tauri dev`/`cargo run` still works
for iterating on everything else (menu, icons, hook logic), just without
notifications; the pulsing tray icon still works either way.
```bash
npm run tauri build -- --debug   # fast iteration build
# or, for a real release build:
npm run tauri icon src-tauri/icons/icon.png   # regenerate app icon sizes first
npm run tauri build
open "src-tauri/target/debug/bundle/macos/Claude Pill.app"   # (or .../release/... )
```

**5. Restart Claude Code** sessions so they pick up the hooks.

## Things to know

- **Timeouts are layered on purpose:** widget waits 280s → hook HTTP timeout
  290s → hook timeout in settings.json 300s. If nothing resolves it in time,
  everything falls through to the normal terminal prompt.
- Plan-mode questions and multi-choice prompts surface via the `Notification`
  hook as an "attention" status on the session; answer those in the terminal
  — click the session row to jump to VS Code.
- Port is hardcoded to `7777` in `src-tauri/src/main.rs` (`HTTP_ADDR`) and
  both hook scripts — change all three together.
- Menu item icons only support a leading (left) image — that's a hard
  `NSMenuItem` limitation, not something we chose; a trailing icon would mean
  dropping to a fully custom `NSView`-based item.
- Hook payload fields and the `PreToolUse` decision schema are current as of
  mid-2026; if a Claude Code update changes them, check
  https://docs.claude.com/en/docs/claude-code (hooks reference) and adjust
  `claude_pill_pretooluse.py`.

## Roadmap ideas

- "Allow & remember" — write the matched pattern straight into
  `permissions.allow` from the menu, so repeated approvals taper off
- Allow/Deny as real action buttons on the notification itself
  (`mac-notification-sys` supports this), not just click-to-open-menu
- Launch at login
- Windows (toast fallback) and Linux builds — the Rust/hook core not tied to
  a window is already most of the way there
