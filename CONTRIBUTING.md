# Contributing to Pill

Thanks for taking a look. This doc covers how the project is put together,
how to run it locally while you're changing something, and a handful of
platform gotchas that cost real time to figure out the first time around —
worth reading before you rediscover them.

## Project layout

```
src-tauri/src/main.rs         the whole app: HTTP server, tray icon, native
                              menu, notifications, launch-at-login
hooks/pill_pretooluse.py      PreToolUse hook: permission-rule matching,
                              live permission-mode handling, frontmost-window
                              check, talks to the app over HTTP
hooks/pill_event.py           fire-and-forget session status hook
hooks/settings.snippet.json   hook registration template for ~/.claude/settings.json
hooks/install.py              end-user setup script (see below), bundled into
                              the app -- not run as part of building it
hooks/test_pill_pretooluse.py plain-assert self-check for the hook's matching logic
hooks/test_install.py         plain-assert self-check for install.py's merge logic
src-tauri/icons/              tray icons, mode badges, app icon (see below —
                              these are hand-generated, not arbitrary PNGs)
```

There's no frontend — no window, no webview, no JS at all. Everything is a
native `NSStatusItem` + `NSMenu`, built directly with `tauri::tray` /
`tauri::menu`.

Everything under `hooks/` ships inside the built app too, at
`Pill.app/Contents/Resources/hooks/` (see `bundle.resources` in
`tauri.conf.json`) — that's what `hooks/install.py` copies from when an end
user runs it after installing a release build. If you add a new file under
`hooks/` that end users need, add it to that resources map too, or it'll
work for you locally (symlinked) and silently be missing for anyone who
just downloaded the app.

## How it works

The app runs a local HTTP server on `127.0.0.1:7777`. Claude Code hooks talk
to it:

- **`PreToolUse` → `POST /approval`, and blocks.** Before ever contacting the
  app, `pill_pretooluse.py` checks three things, any of which lets it exit
  silently and leave Claude Code's own flow to handle the rest:
  1. Does an existing `permissions.allow`/`deny`/`ask` rule in settings.json
     already cover this call? (`already_settled()` replicates Claude Code's
     own rule matching: Bash prefix/wildcard patterns, gitignore-style file
     globs, domain wildcards for WebFetch.)
  2. Is the live session permission mode (`default`/`acceptEdits`/`auto`/
     `bypassPermissions`/`plan` — read straight from the hook payload's
     `permission_mode` field, since this is toggled at runtime via Shift+Tab
     and never written to settings.json) already permissive enough?
  3. Is the frontmost app + window title already the project in question
     (`relevant_window_is_frontmost()`, via a `System Events` AppleScript
     query)? If you're already looking at the right editor/terminal, the
     terminal prompt is simpler than routing through a menu.

  If none of those settle it, the request reaches the app: the tray menu
  gets an Allow/Deny/Answer-in-Terminal row, and a notification fires (with
  real action buttons, sound, and a retry loop re-announcing every ~12s
  until it's resolved — legacy `NSUserNotificationCenter` banners auto-dismiss
  in a couple seconds, so one announcement is easy to miss entirely). The
  hook returns the decision to Claude Code as `hookSpecificOutput.permissionDecision`.
  Answering in the terminal instead (or a timeout, or the app not running at
  all) makes the hook exit with no output, so Claude Code falls through to
  its normal terminal prompt — the app can never lock a session out.
- **`SessionStart`/`UserPromptSubmit`/`Stop`/`Notification`/`SessionEnd` →
  fire-and-forget `POST /event`** (`pill_event.py`), which is how the menu
  knows each session's repo, status, and current permission mode.

## Running it locally

```bash
npm install
npm run tauri dev
```

This gives you fast iteration on the menu, icons, and hook logic. **Real
macOS notifications will not work in dev mode** — the legacy notification API
this app uses (`mac-notification-sys`) can't post under an impersonated
identity from an unbundled dev binary, only from a genuine `.app`. The tray
icon's pulsing glow still works either way, so most changes are still
visible without a full build.

To test notifications specifically, build and run the real bundle:
```bash
npm run tauri build -- --debug
open src-tauri/target/debug/bundle/macos/Pill.app
```

**Point Claude Code at your working copy** by symlinking the hooks (not
copying — this way your edits take effect immediately, no rebuild or
redeploy needed):
```bash
mkdir -p ~/.claude/hooks
ln -s "$(pwd)/hooks/pill_pretooluse.py" ~/.claude/hooks/pill_pretooluse.py
ln -s "$(pwd)/hooks/pill_event.py" ~/.claude/hooks/pill_event.py
```
Then merge `hooks/settings.snippet.json` into `~/.claude/settings.json`
(append to existing arrays if you already have other hooks configured,
rather than replacing them). This is the dev-time equivalent of what
`hooks/install.py` does for end users from a built release — see below.

## Tests

```bash
python3 hooks/test_pill_pretooluse.py   # hook matching logic
python3 hooks/test_install.py           # end-user setup script's merge logic
cargo build --manifest-path src-tauri/Cargo.toml   # Rust compiles clean
```

There's no Rust test suite beyond compilation — the interesting logic (rule
matching, permission modes, frontmost-window detection) lives in the Python
hook and is covered there. If you add non-trivial branching logic anywhere,
add it to the relevant test file rather than skipping coverage.

## Gotchas worth knowing before you dig in

These are all things that looked like the wrong bug for a while before the
real cause turned up — hopefully this saves you the loop.

- **`qlmanage`'s SVG rasterizer flattens transparent backgrounds to opaque
  white.** If you regenerate any icon from an SVG via `qlmanage -t`, you'll
  get a solid square instead of a transparent-background glyph, even though
  the file reports `hasAlpha: yes` — the alpha channel exists, it's just all
  255. The icons in this repo are written directly as PNGs via a small
  pure-Python script (signed distance field + manual PNG/zlib encoding, no
  Pillow needed for the tray icons; the mode-badge *chips* with text baked in
  do use Pillow, in an isolated venv, since real text needs real font
  rendering). If you need to regenerate or add an icon, follow that pattern
  rather than reaching for a system SVG renderer.
- **`tray-icon`'s `set_title(None)` is a no-op on macOS** (confirmed by
  reading the crate source — the `None` branch just doesn't call
  `setTitle` at all). If you need a piece of tray-icon state to reliably
  clear, don't rely on titles; swap the icon image instead, the way the
  pending-approval glow does.
- **Legacy `NSUserNotificationCenter` can't post under a borrowed
  identity from an unbundled binary.** `mac-notification-sys` lets you
  `set_application("some.bundle.id")` to post as a different app, but this
  only actually works from within a real, launched `.app` bundle — a raw
  `cargo run`/`tauri dev` process trying to impersonate *any* identity
  (including its own eventual bundle id) silently fails to display anything,
  with no error. `running_from_app_bundle()` in `main.rs` detects this and
  skips notifications entirely in dev mode rather than pretending to work.
- **`open -a "Some App" <path>` doesn't reuse an already-open window** the
  way the app's own CLI does — it goes through macOS's generic document-open
  path, which skips VS Code's "already open here? focus that" logic. Hitting
  the app's bundled CLI script directly (see `open_project()` in `main.rs`)
  is what actually reuses windows.
- **`NSMenuItem` icons are leading (left) only** — there's no public API for
  a trailing icon on a plain menu item. Getting content on the right side of
  a row would mean a fully custom `NSView`-based item, which is a real jump
  in complexity and fragility versus the plain builder API this project
  currently uses throughout.
- **Hooks must be symlinked, not copied**, and the same applies to
  `~/.claude/settings.json`'s hook *registration* (the JSON, as opposed to
  the `.py` files themselves) — that part genuinely isn't kept in sync
  automatically, so if you change `hooks/settings.snippet.json`, re-merge it
  into `~/.claude/settings.json` by hand.
- **"Pill is damaged and can't be opened" on a fresh download is expected,
  not a build bug.** Rust's linker already ad-hoc-signs the binary on Apple
  Silicon (`codesign -dv` shows `Signature=adhoc`), but there's no real
  Developer ID behind it (`TeamIdentifier=not set`), so a quarantined
  (browser-downloaded) copy fails Gatekeeper outright instead of falling
  back to the milder "unidentified developer" prompt. The only real fixes
  are `xattr -cr` (what the README/release notes tell users to run) or
  actually notarizing with a paid Apple Developer ID — there's no free way
  to make this fully clean.

## Code style

Lean on purpose: standard library and native platform features before a new
dependency, no abstractions or config for things that only have one caller
or one value. If a shortcut has a known ceiling (a hardcoded port, a
best-effort heuristic like the frontmost-window check), a short comment
naming the ceiling is more useful than either silence or a defensive
rewrite — see the existing comments in `main.rs` for the pattern.

## Submitting changes

- Keep commits focused and describe the *why*, not just the *what*.
- Run the checks in the Tests section above before opening a PR.
- If you're changing hook payload assumptions (field names, decision
  schema), note it in the PR — Claude Code's hook interface can change
  between versions, and `pill_pretooluse.py`'s assumptions are pinned to
  what's documented as of mid-2026.
