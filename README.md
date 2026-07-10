# Claude Pill

A tiny always-on-top widget for macOS (Tauri, so Windows/Linux later) that follows you across every Space and full-screen app. It shows what your Claude Code sessions are doing, pops open the moment Claude is blocked on an approval, and lets you **Allow / Deny right from the widget** — no tabbing back to the terminal. Clicking a session opens its repo in VS Code.

```
collapsed:   [⋮⋮  ● 1 approval · my-repo  ▾]

expanded:    ┌──────────────────────────────┐
             │ ● my-repo          WAITING   │  ← click = open in VS Code
             │   /Users/me/dev/my-repo      │
             │   [Bash]                     │
             │   rm -rf node_modules && …   │
             │   [Allow] [Deny] [Terminal]  │
             ├──────────────────────────────┤
             │ ● other-repo       WORKING   │
             └──────────────────────────────┘
```

## How it works

- The widget runs a local HTTP server on `127.0.0.1:7777`.
- Claude Code **hooks** (configured in `~/.claude/settings.json`) talk to it:
  - `PreToolUse` → POSTs the pending tool call to `/approval` and **blocks**. The widget shows Allow / Deny / Terminal; the hook returns the decision to Claude Code (`permissionDecision: allow|deny`). "Terminal" (or a timeout, or the widget not running) makes the hook exit silently, so Claude Code falls back to its normal terminal prompt — the widget can never lock you out.
  - `SessionStart / UserPromptSubmit / Stop / Notification / SessionEnd` → fire-and-forget POSTs to `/event`, which is how the pill knows each session's repo (`cwd`), session ID, and status (working / idle / needs attention).
- "Open in VS Code" runs `open -a "Visual Studio Code" <cwd>` (macOS) or `code <cwd>` elsewhere.

## Setup

**1. Prerequisites:** Rust (`rustup`), Node.js, Xcode Command Line Tools.

**2. Run the widget**
```bash
cd claude-pill
npm install
npm run dev        # tauri dev — the pill appears; drag it by the ⋮⋮ grip
```

**3. Install the hooks**
```bash
mkdir -p ~/.claude/hooks
cp hooks/claude_pill_pretooluse.py hooks/claude_pill_event.py ~/.claude/hooks/
```
Then merge `hooks/settings.snippet.json` into `~/.claude/settings.json` (create it if it doesn't exist). If you already have hooks configured, append these entries to the matching arrays rather than replacing them.

**4. Restart Claude Code** sessions so they pick up the hooks. Start a task, switch away — when Claude wants to run something, the pill flashes amber and expands with the request.

**5. Building a release app:** `npm run tauri icon src-tauri/icons/icon.png` (generates all icon sizes), then `npm run tauri build`.

## Things to know

- **The PreToolUse matcher intercepts listed tools even if your permission rules would auto-allow them.** The snippet matches `Bash|Write|Edit|MultiEdit|NotebookEdit|WebFetch`. If you've allowed e.g. all `Edit` operations in your Claude Code permissions and don't want the widget asking, remove `Edit` from the matcher. Tune it to taste.
- **Timeouts are layered on purpose:** widget waits 280s → hook HTTP timeout 290s → hook timeout in settings.json 300s. If you don't respond in time, everything falls through to the normal terminal prompt.
- Plan-mode questions and multi-choice prompts surface via the `Notification` hook as an "attention" state on the session (with the message shown); v1 answers those in the terminal — click the session to jump there.
- The pill floats above normal windows on all Spaces (`alwaysOnTop` + `visibleOnAllWorkspaces`). To float above *native full-screen* apps too, the upgrade path is the `tauri-nspanel` plugin (turns the window into an NSPanel).
- Port is hardcoded to `7777` in `src-tauri/src/main.rs` (`HTTP_ADDR`) and both hook scripts — change all three together.
- Hook payload fields and the `PreToolUse` decision schema are current as of mid-2026; if a Claude Code update changes them, check https://docs.claude.com/en/docs/claude-code (hooks reference) and adjust `claude_pill_pretooluse.py`.

## Roadmap ideas

- Per-session "auto-allow for this session" rules in the widget
- Answering multi-option / plan questions from the widget
- Windows (PowerShell toast fallback) and Linux builds — the Rust/hook core is already cross-platform
- Menu bar icon + launch at login
