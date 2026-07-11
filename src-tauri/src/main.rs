// Pill — a menu bar companion for Claude Code
// Runs a local HTTP server that Claude Code hooks talk to:
//   POST /event    -> fire-and-forget session status updates
//   POST /approval -> BLOCKS until the user decides in the menu (or times out)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::menu::{CheckMenuItemBuilder, IconMenuItemBuilder, MenuBuilder, MenuItemBuilder, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;

const ICON_IDLE: tauri::image::Image<'_> = tauri::include_image!("./icons/tray-idle.png");
const ICON_PENDING_BRIGHT: tauri::image::Image<'_> =
    tauri::include_image!("./icons/tray-pending-bright.png");
const ICON_PENDING_DIM: tauri::image::Image<'_> =
    tauri::include_image!("./icons/tray-pending-dim.png");

const MODE_ICON_DEFAULT: tauri::image::Image<'_> = tauri::include_image!("./icons/mode-default.png");
const MODE_ICON_ACCEPT_EDITS: tauri::image::Image<'_> =
    tauri::include_image!("./icons/mode-accept-edits.png");
const MODE_ICON_AUTO: tauri::image::Image<'_> = tauri::include_image!("./icons/mode-auto.png");
const MODE_ICON_BYPASS: tauri::image::Image<'_> = tauri::include_image!("./icons/mode-bypass.png");
const MODE_ICON_PLAN: tauri::image::Image<'_> = tauri::include_image!("./icons/mode-plan.png");

const HTTP_ADDR: &str = "127.0.0.1:7777";
// Keep this below the hook "timeout" in settings.json (300s) so we answer
// with "passthrough" before Claude Code kills the hook.
const APPROVAL_WAIT_SECS: u64 = 280;

#[derive(Clone)]
struct Session {
    id: String,
    cwd: String,
    status: String, // "idle" | "working" | "waiting" | "attention"
    mode: String, // "default" | "acceptEdits" | "auto" | "bypassPermissions" | "plan"
    updated_at: u64,
}

fn mode_icon(mode: &str) -> tauri::image::Image<'static> {
    match mode {
        "acceptEdits" => MODE_ICON_ACCEPT_EDITS,
        "auto" => MODE_ICON_AUTO,
        "bypassPermissions" => MODE_ICON_BYPASS,
        "plan" => MODE_ICON_PLAN,
        _ => MODE_ICON_DEFAULT,
    }
}

#[derive(Clone)]
struct Approval {
    id: String,
    session_id: String,
    cwd: String,
    tool_name: String,
    summary: String,
    created_at: u64,
}

#[derive(Default)]
struct Store {
    sessions: HashMap<String, Session>,
    approvals: HashMap<String, Approval>,
    waiters: HashMap<String, mpsc::Sender<Value>>,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn basename(path: &str) -> &str {
    let trimmed = path.trim_end_matches('/');
    trimmed.rsplit('/').next().filter(|s| !s.is_empty()).unwrap_or(path)
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}

fn decide(store: &Arc<Mutex<Store>>, id: &str, decision: &str) {
    let tx = { store.lock().unwrap().waiters.remove(id) };
    if let Some(tx) = tx {
        let _ = tx.send(json!({
            "decision": decision, // "allow" | "deny" | "passthrough"
            "reason": "Decided in Pill"
        }));
    }
}

fn open_project(cwd: &str) {
    if cwd.is_empty() {
        return;
    }
    #[cfg(target_os = "macos")]
    {
        // `open -a` goes through macOS's generic document-open path, which
        // skips VS Code's own "already open somewhere? focus that window"
        // logic -- that only kicks in via its `code` CLI. Hit the CLI
        // script directly so it works even without `code` on PATH (a GUI
        // launched app often won't have the user's shell PATH anyway).
        const VSCODE_CLI: &str = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code";
        let spawned = if std::path::Path::new(VSCODE_CLI).exists() {
            std::process::Command::new(VSCODE_CLI).arg(cwd).spawn()
        } else {
            std::process::Command::new("code").arg(cwd).spawn()
        };
        if spawned.is_err() {
            let _ = std::process::Command::new("open")
                .args(["-a", "Visual Studio Code", cwd])
                .spawn();
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = std::process::Command::new("code").arg(cwd).spawn();
    }
}

// Legacy NSUserNotificationCenter can't post under an arbitrary bundle's
// identity from a bare dev binary -- only a real .app bundle running its
// own executable can. Below that, notifications silently no-op; the tray
// icon's pulsing glow is the fallback signal in dev mode.
#[cfg(target_os = "macos")]
fn running_from_app_bundle() -> bool {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().contains(".app/Contents/MacOS/"))
        .unwrap_or(false)
}

// ------------------------------------------------------------- tray animation

const PULSE_INTERVAL_MS: u64 = 600;
static ANIMATING: AtomicBool = AtomicBool::new(false);

// Pulses the tray icon between a bright and dim orange glow while any
// approval is pending, reverting to the plain idle icon once none remain.
// (tray-icon's set_title(None) is a no-op on macOS, so an icon swap -- not
// title text -- is what actually clears reliably.) The AtomicBool guard
// keeps this to one thread even if several approvals arrive close together.
fn start_pending_glow(app: &AppHandle, store: Arc<Mutex<Store>>) {
    if ANIMATING.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let Some(tray) = app.tray_by_id("main") else {
            ANIMATING.store(false, Ordering::SeqCst);
            return;
        };
        let _ = tray.set_icon_as_template(false);
        let mut bright = true;
        while !store.lock().unwrap().approvals.is_empty() {
            let icon = if bright { ICON_PENDING_BRIGHT } else { ICON_PENDING_DIM };
            let _ = tray.set_icon(Some(icon));
            bright = !bright;
            std::thread::sleep(Duration::from_millis(PULSE_INTERVAL_MS));
        }
        let _ = tray.set_icon(Some(ICON_IDLE));
        let _ = tray.set_icon_as_template(true);
        ANIMATING.store(false, Ordering::SeqCst);
    });
}

// --------------------------------------------------------------- notification

const NOTIFY_RETRY_SECS: u64 = 12;

// Fires a real macOS notification and blocks (on its own thread) waiting to
// see if the user acts on it -- Allow/Deny are real buttons on the banner
// itself (main button + close button), so most approvals never need the
// tray menu at all. Clicking the notification body (not a button) instead
// pops the tray menu open, for "Answer in Terminal" or just more context.
//
// Banners auto-dismiss in a couple seconds regardless of anything we do, so
// one notification is easy to miss. Keep re-announcing until the approval
// is actually decided (however that happens).
#[cfg(target_os = "macos")]
fn notify_approval_pending(app: &AppHandle, store: Arc<Mutex<Store>>, approval_id: String, title: String, body: String) {
    if !running_from_app_bundle() {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        while store.lock().unwrap().waiters.contains_key(&approval_id) {
            let mut opts = mac_notification_sys::Notification::new();
            opts.wait_for_click(true);
            opts.default_sound();
            opts.main_button(mac_notification_sys::MainButton::SingleAction("Allow"));
            opts.close_button("Deny");
            let response = mac_notification_sys::send_notification(&title, None, &body, Some(&opts));
            match response {
                Ok(mac_notification_sys::NotificationResponse::ActionButton(_)) => {
                    decide(&store, &approval_id, "allow");
                    return;
                }
                Ok(mac_notification_sys::NotificationResponse::CloseButton(_)) => {
                    decide(&store, &approval_id, "deny");
                    return;
                }
                Ok(mac_notification_sys::NotificationResponse::Click) => {
                    if let Some(tray) = app.tray_by_id("main") {
                        let _ = tray.with_inner_tray_icon(|icon| icon.show_menu());
                    }
                    return;
                }
                _ => {}
            }
            std::thread::sleep(Duration::from_secs(NOTIFY_RETRY_SECS));
        }
    });
}

// Windows toast, no action buttons yet (see roadmap) -- clicking the toast
// body (the only click there is, for now) pops the tray menu open, same as
// the mac Click case below. Toasts are event-driven rather than blocking
// like mac_notification_sys, so a channel stands in for the blocking wait:
// on_activated fires on a WinRT callback thread and signals the retry loop
// via `tx` rather than returning a value directly.
//
// ponytail: AUMID is reused from tauri.conf.json's identifier, unverified
// against a real Windows box (branding/whether it silently no-ops without a
// registered Start Menu shortcut needs a manual check on the Windows VM).
// Upgrade path to real Allow/Deny buttons is `add_button` + branching on
// `Some(action)` in on_activated -- deferred until this baseline is confirmed
// working.
#[cfg(target_os = "windows")]
fn notify_approval_pending(app: &AppHandle, store: Arc<Mutex<Store>>, approval_id: String, title: String, body: String) {
    let app = app.clone();
    std::thread::spawn(move || {
        while store.lock().unwrap().waiters.contains_key(&approval_id) {
            let (tx, rx) = mpsc::channel::<bool>();
            let app_cb = app.clone();
            let shown = tauri_winrt_notification::Toast::new("com.pill.widget")
                .title(&title)
                .text1(&body)
                .on_activated(move |action| {
                    if action.is_none() {
                        // body click, no button -- same as mac: stop
                        // re-announcing and let the tray menu take over
                        if let Some(tray) = app_cb.tray_by_id("main") {
                            let _ = tray.with_inner_tray_icon(|icon| icon.show_menu());
                        }
                        let _ = tx.send(true);
                    }
                    Ok(())
                })
                .show();
            if shown.is_err() {
                break; // toast API unavailable -- tray glow is the fallback signal
            }
            if rx.recv_timeout(Duration::from_secs(NOTIFY_RETRY_SECS)) == Ok(true) {
                break;
            }
        }
    });
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn notify_approval_pending(
    _app: &AppHandle,
    _store: Arc<Mutex<Store>>,
    _approval_id: String,
    _title: String,
    _body: String,
) {
}

// ------------------------------------------------------------------ the menu

// Native NSMenu on the tray icon — a plain list, same as Wi-Fi/Bluetooth/etc,
// so it just works across Spaces with no window of our own to fight with.
fn rebuild_menu(app: &AppHandle, store: &Arc<Mutex<Store>>) {
    let (mut approvals, mut sessions) = {
        let s = store.lock().unwrap();
        (
            s.approvals.values().cloned().collect::<Vec<_>>(),
            s.sessions.values().cloned().collect::<Vec<_>>(),
        )
    };
    approvals.sort_by_key(|a| a.created_at);
    sessions.sort_by_key(|s| std::cmp::Reverse(s.updated_at));

    let mut menu = MenuBuilder::new(app);

    if approvals.is_empty() && sessions.is_empty() {
        menu = menu.text("none", "No Claude Code sessions yet");
    }

    for a in &approvals {
        let header = format!(
            "{} · {}: {}",
            basename(&a.cwd),
            a.tool_name,
            truncate(&a.summary, 44)
        );
        let header_item = MenuItemBuilder::new(header).enabled(false).build(app).unwrap();
        menu = menu
            .item(&header_item)
            .text(format!("allow:{}", a.id), "Allow")
            .text(format!("deny:{}", a.id), "Deny")
            .text(format!("term:{}", a.id), "Answer in Terminal")
            .separator();
    }

    for s in &sessions {
        if approvals.iter().any(|a| a.session_id == s.id) {
            continue; // already shown above with its approval actions
        }
        let label = format!("{} — {}", basename(&s.cwd), s.status);
        let row = IconMenuItemBuilder::with_id(format!("open:{}", s.id), label)
            .icon(mode_icon(&s.mode))
            .build(app)
            .unwrap();
        menu = menu.item(&row);
    }

    let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart_item = CheckMenuItemBuilder::with_id("toggle-autostart", "Launch at Login")
        .checked(autostart_enabled)
        .build(app)
        .unwrap();
    let quit_item = PredefinedMenuItem::quit(app, Some("Quit Pill")).unwrap();
    let menu = menu
        .separator()
        .item(&autostart_item)
        .item(&quit_item)
        .build()
        .unwrap();

    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_menu(Some(menu));
    }
}

// ---------------------------------------------------------------- HTTP side

fn handle_event(app: &AppHandle, store: &Arc<Mutex<Store>>, body: &Value) {
    let event = body["hook_event_name"].as_str().unwrap_or("");
    let session_id = body["session_id"].as_str().unwrap_or("unknown").to_string();
    let cwd = body["cwd"].as_str().unwrap_or("").to_string();
    let mode = body["permission_mode"].as_str().unwrap_or("").to_string();

    {
        let mut s = store.lock().unwrap();
        if event == "SessionEnd" {
            s.sessions.remove(&session_id);
        } else {
            let status = match event {
                "SessionStart" => "idle",
                "UserPromptSubmit" | "PreToolUse" | "PostToolUse" => "working",
                "Stop" => "idle",
                "Notification" => "attention",
                _ => "idle",
            }
            .to_string();
            let entry = s.sessions.entry(session_id.clone()).or_insert(Session {
                id: session_id.clone(),
                cwd: cwd.clone(),
                status: status.clone(),
                mode: mode.clone(),
                updated_at: now_ms(),
            });
            entry.status = status;
            if !cwd.is_empty() {
                entry.cwd = cwd;
            }
            if !mode.is_empty() {
                entry.mode = mode;
            }
            entry.updated_at = now_ms();
        }
    }
    rebuild_menu(app, store);
}

fn handle_approval(app: &AppHandle, store: &Arc<Mutex<Store>>, body: &Value) -> Value {
    let session_id = body["session_id"].as_str().unwrap_or("unknown").to_string();
    let cwd = body["cwd"].as_str().unwrap_or("").to_string();
    let tool_name = body["tool_name"].as_str().unwrap_or("Tool").to_string();
    let summary = body["summary"].as_str().unwrap_or("").to_string();
    let mode = body["permission_mode"].as_str().unwrap_or("").to_string();
    let id = format!("apr-{}-{}", now_ms(), &session_id.chars().take(8).collect::<String>());

    let (tx, rx) = mpsc::channel::<Value>();
    {
        let mut s = store.lock().unwrap();
        s.approvals.insert(
            id.clone(),
            Approval {
                id: id.clone(),
                session_id: session_id.clone(),
                cwd: cwd.clone(),
                tool_name: tool_name.clone(),
                summary: summary.clone(),
                created_at: now_ms(),
            },
        );
        s.waiters.insert(id.clone(), tx);
        let entry = s.sessions.entry(session_id.clone()).or_insert(Session {
            id: session_id.clone(),
            cwd: cwd.clone(),
            status: "waiting".into(),
            mode: mode.clone(),
            updated_at: now_ms(),
        });
        entry.status = "waiting".into();
        if !mode.is_empty() {
            entry.mode = mode;
        }
        entry.updated_at = now_ms();
    }
    rebuild_menu(app, store);
    start_pending_glow(app, store.clone());
    notify_approval_pending(
        app,
        store.clone(),
        id.clone(),
        format!("Claude needs your OK — {}", basename(&cwd)),
        format!("{}: {}", tool_name, truncate(&summary, 120)),
    );

    // Block this request thread until the user decides, or time out to
    // "passthrough" so Claude Code falls back to its normal terminal prompt.
    let decision = rx
        .recv_timeout(Duration::from_secs(APPROVAL_WAIT_SECS))
        .unwrap_or_else(|_| json!({ "decision": "passthrough", "reason": "Timed out in Pill" }));

    {
        let mut s = store.lock().unwrap();
        s.approvals.remove(&id);
        s.waiters.remove(&id);
        let still_waiting = s.approvals.values().any(|a| a.session_id == session_id);
        if !still_waiting {
            if let Some(sess) = s.sessions.get_mut(&session_id) {
                sess.status = "working".into();
                sess.updated_at = now_ms();
            }
        }
    }
    rebuild_menu(app, store);
    decision
}

fn run_http(app: AppHandle, store: Arc<Mutex<Store>>) {
    let server = match tiny_http::Server::http(HTTP_ADDR) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Pill: could not bind {HTTP_ADDR}: {e}");
            return;
        }
    };
    for mut request in server.incoming_requests() {
        let app = app.clone();
        let store = store.clone();
        // One thread per request: /approval blocks for minutes by design.
        std::thread::spawn(move || {
            let mut body = String::new();
            let _ = request.as_reader().read_to_string(&mut body);
            let parsed: Value = serde_json::from_str(&body).unwrap_or(json!({}));
            let url = request.url().to_string();

            let reply = match url.as_str() {
                "/approval" => handle_approval(&app, &store, &parsed),
                "/event" => {
                    handle_event(&app, &store, &parsed);
                    json!({ "ok": true })
                }
                _ => json!({ "error": "not found" }),
            };

            let header =
                tiny_http::Header::from_bytes(&b"Content-Type"[..], &b"application/json"[..])
                    .unwrap();
            let _ = request.respond(
                tiny_http::Response::from_string(reply.to_string()).with_header(header),
            );
        });
    }
}

fn main() {
    let store: Arc<Mutex<Store>> = Arc::default();
    let store_for_setup = store.clone();
    let store_for_menu = store.clone();

    tauri::Builder::default()
        .on_menu_event(move |app, event| {
            let id = event.id().0.as_str();
            if let Some(rest) = id.strip_prefix("allow:") {
                decide(&store_for_menu, rest, "allow");
            } else if let Some(rest) = id.strip_prefix("deny:") {
                decide(&store_for_menu, rest, "deny");
            } else if let Some(rest) = id.strip_prefix("term:") {
                let cwd = store_for_menu.lock().unwrap().approvals.get(rest).map(|a| a.cwd.clone());
                if let Some(cwd) = cwd {
                    open_project(&cwd);
                }
                decide(&store_for_menu, rest, "passthrough");
            } else if let Some(rest) = id.strip_prefix("open:") {
                let cwd = store_for_menu.lock().unwrap().sessions.get(rest).map(|s| s.cwd.clone());
                if let Some(cwd) = cwd {
                    open_project(&cwd);
                }
            } else if id == "toggle-autostart" {
                let autolaunch = app.autolaunch();
                let result = if autolaunch.is_enabled().unwrap_or(false) {
                    autolaunch.disable()
                } else {
                    autolaunch.enable()
                };
                if let Err(e) = result {
                    eprintln!("Pill: could not toggle launch at login: {e}");
                }
            }
            rebuild_menu(app, &store_for_menu);
        })
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(move |app| {
            // Menu bar accessory, not a Dock app: this is a tray-only utility,
            // no window of its own at all.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            // Only a real .app bundle can post notifications under its own
            // identity; see running_from_app_bundle().
            #[cfg(target_os = "macos")]
            if running_from_app_bundle() {
                let _ = mac_notification_sys::set_application("com.pill.widget");
            }

            TrayIconBuilder::with_id("main")
                .icon(ICON_IDLE)
                .icon_as_template(true)
                .tooltip("Pill")
                .build(app)?;
            rebuild_menu(app.handle(), &store_for_setup);

            let handle = app.handle().clone();
            let http_store = store_for_setup.clone();
            std::thread::spawn(move || run_http(handle, http_store));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Pill");
}
