//! Native shell for the J.A.R.V.I.S. command center.
//!
//! Responsibilities kept deliberately small:
//! - hand the owner token (read from disk, never logged) to the webview,
//! - menu bar (tray) icon with quick actions,
//! - global shortcut to summon the window,
//! - hide-to-tray on close, optional launch at login.

mod core_client;

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// Tracks whether push-to-talk was started from the tray menu (toggle semantics).
static TRAY_PTT_ACTIVE: AtomicBool = AtomicBool::new(false);

/// Returns the owner device token for the local core. The value is never logged.
#[tauri::command]
fn get_core_token() -> Result<String, String> {
    let token = core_client::read_token()?;
    println!("[jarvis-desktop] core token loaded ({} chars)", token.len());
    Ok(token)
}

/// Lets the frontend write short diagnostic lines to stdout (used for headless smoke tests).
/// Callers must never pass secrets here.
#[tauri::command]
fn frontend_log(message: String) {
    let clipped: String = message.chars().take(300).collect();
    println!("[jarvis-desktop/ui] {clipped}");
}

fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Öffnen", true, None::<&str>)?;
    let ptt = MenuItem::with_id(app, "ptt", "Push-to-Talk starten", true, None::<&str>)?;
    let kill = MenuItem::with_id(app, "killswitch", "Not-Aus", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Beenden", true, Some("CmdOrCtrl+Q"))?;
    let sep_a = PredefinedMenuItem::separator(app)?;
    let sep_b = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &ptt, &sep_a, &kill, &sep_b, &quit])?;

    let ptt_item = ptt.clone();
    TrayIconBuilder::with_id("jarvis-tray")
        .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
        .icon_as_template(true)
        .tooltip("J.A.R.V.I.S.")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => show_main_window(app),
            "ptt" => {
                let starting = !TRAY_PTT_ACTIVE.load(Ordering::SeqCst);
                let action = if starting { "ptt_start" } else { "ptt_stop" };
                TRAY_PTT_ACTIVE.store(starting, Ordering::SeqCst);
                let _ = ptt_item.set_text(if starting {
                    "Push-to-Talk beenden"
                } else {
                    "Push-to-Talk starten"
                });
                let handle = app.clone();
                let body = format!("{{\"action\":\"{action}\"}}");
                std::thread::spawn(move || {
                    let outcome = core_client::post_json("/api/voice/command", &body);
                    report(&handle, action, outcome);
                });
            }
            "killswitch" => {
                let handle = app.clone();
                std::thread::spawn(move || {
                    let outcome = core_client::post_json(
                        "/api/system/killswitch",
                        r#"{"engaged":true,"reason":"Not-Aus über die Menüleiste"}"#,
                    );
                    report(&handle, "killswitch", outcome);
                });
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

/// Forwards the outcome of a tray action to the webview (toast + refresh) and stdout.
fn report(app: &AppHandle, action: &str, outcome: Result<u16, String>) {
    let (ok, detail) = match outcome {
        Ok(code) if (200..300).contains(&code) => (true, format!("HTTP {code}")),
        Ok(code) => (false, format!("HTTP {code}")),
        Err(e) => (false, e),
    };
    println!("[jarvis-desktop] tray action {action}: ok={ok} ({detail})");
    let _ = app.emit(
        "tray-action",
        serde_json::json!({ "action": action, "ok": ok, "detail": detail }),
    );
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let summon = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyJ);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if shortcut == &summon && event.state() == ShortcutState::Pressed {
                        show_main_window(app);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![get_core_token, frontend_log])
        .setup(move |app| {
            build_tray(app)?;
            if let Err(e) = app.global_shortcut().register(summon) {
                eprintln!("[jarvis-desktop] global shortcut Cmd+Shift+J unavailable: {e}");
            }
            println!("[jarvis-desktop] started, core at {}", core_client::CORE_ADDR);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window hides it to the menu bar; "Beenden" in the tray quits.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the J.A.R.V.I.S. desktop app");

    app.run(|handle, event| {
        #[cfg(target_os = "macos")]
        if let RunEvent::Reopen { .. } = event {
            show_main_window(handle);
        }
        #[cfg(not(target_os = "macos"))]
        let _ = (handle, event);
    });
}
