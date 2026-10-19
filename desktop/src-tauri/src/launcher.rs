//! Starts and stops the J.A.R.V.I.S. background services (whisper, JarvisBridge, core, voice
//! daemon) together with the desktop app, so Sir never has to use the terminal.
//!
//! The services live in the project checkout (Python venv, models, config). Its location is read
//! from `~/Library/Application Support/Jarvis/root` (written by `scripts/install-app.sh`), falling
//! back to the checkout this binary was built from. Output goes to `~/Library/Logs/Jarvis/launcher.log`.

use std::fs::OpenOptions;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use tauri::{AppHandle, Emitter};

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}

pub fn project_root() -> Option<PathBuf> {
    let configured = home().join("Library/Application Support/Jarvis/root");
    if let Ok(text) = std::fs::read_to_string(&configured) {
        let p = PathBuf::from(text.trim());
        if p.join("scripts/start.sh").is_file() {
            return Some(p);
        }
    }
    let built_from = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    built_from
        .join("scripts/start.sh")
        .is_file()
        .then(|| built_from.canonicalize().unwrap_or(built_from))
}

fn run_script(name: &str, args: &[&str]) -> Result<(), String> {
    let root = project_root().ok_or("Jarvis-Projektordner nicht gefunden")?;
    let logs = home().join("Library/Logs/Jarvis");
    let _ = std::fs::create_dir_all(&logs);
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("launcher.log"))
        .map_err(|e| e.to_string())?;
    let err = log.try_clone().map_err(|e| e.to_string())?;
    // Apps started from Finder get a minimal PATH; the scripts extend it themselves.
    let status = Command::new("/bin/bash")
        .arg(root.join("scripts").join(name))
        .args(args)
        .current_dir(&root)
        .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        .stdin(Stdio::null())
        .stdout(log)
        .stderr(err)
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{name} beendet mit {status} (siehe ~/Library/Logs/Jarvis/launcher.log)"))
    }
}

/// Starts all services in the background and reports the outcome to the webview.
pub fn start_services(app: &AppHandle) {
    let handle = app.clone();
    std::thread::spawn(move || {
        let _ = handle.emit("services", serde_json::json!({ "state": "starting" }));
        let outcome = run_script("start.sh", &["--no-app"]);
        println!("[jarvis-desktop] services start: {outcome:?}");
        let payload = match outcome {
            Ok(()) => serde_json::json!({ "state": "running" }),
            Err(e) => serde_json::json!({ "state": "error", "detail": e }),
        };
        let _ = handle.emit("services", payload);
    });
}

fn keep_flag() -> PathBuf {
    home().join("Library/Application Support/Jarvis/keep-running")
}

/// Whether the services stop when the app quits (default). Opt-out via a flag file next to `root`.
pub fn stop_on_quit() -> bool {
    !keep_flag().exists()
}

pub fn set_stop_on_quit(stop: bool) {
    if stop {
        let _ = std::fs::remove_file(keep_flag());
    } else {
        let _ = std::fs::write(keep_flag(), "");
    }
}

/// Stops the services (called when the app quits). Bounded so quitting never hangs.
pub fn stop_services() {
    let worker = std::thread::spawn(|| run_script("stop.sh", &[]));
    for _ in 0..100 {
        if worker.is_finished() {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}
