//! Starts and stops the J.A.R.V.I.S. background services (whisper, JarvisBridge, core, voice
//! daemon) together with the desktop app, so Sir never has to use the terminal.
//!
//! The services live in the project checkout (Python venv, models, config). Its location is read
//! from `<data dir>/root` (written by `scripts/install-app.sh` / the installer), falling back to the
//! checkout this binary was built from. Output goes to `launcher.log` in the log folder.
//!
//! macOS: `scripts/start.sh` / `stop.sh` (unchanged). Windows: `scripts/windows/start.ps1` / `stop.ps1`.
//! Linux: `scripts/linux/start.sh` / `stop.sh`. When the app is only a client of a remote Core
//! (`client.json`, see core_client.rs) no local service is started or stopped.

use std::fs::OpenOptions;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use tauri::{AppHandle, Emitter};

use crate::core_client;

#[cfg(target_os = "macos")]
fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}

fn data_dir() -> PathBuf {
    core_client::data_dir().unwrap_or_else(|_| std::env::temp_dir().join("Jarvis"))
}

fn logs_dir() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        home().join("Library/Logs/Jarvis")
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var("LOCALAPPDATA")
            .map(|p| PathBuf::from(p).join("Jarvis").join("Logs"))
            .unwrap_or_else(|_| data_dir().join("logs"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        match std::env::var("XDG_STATE_HOME") {
            Ok(s) if !s.is_empty() => PathBuf::from(s).join("jarvis/logs"),
            _ => PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".local/state/jarvis/logs"),
        }
    }
}

/// The start script relative to the project root on this OS.
fn start_script() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        "scripts/start.sh"
    }
    #[cfg(target_os = "windows")]
    {
        "scripts/windows/start.ps1"
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        "scripts/linux/start.sh"
    }
}

pub fn project_root() -> Option<PathBuf> {
    let configured = data_dir().join("root");
    if let Ok(text) = std::fs::read_to_string(&configured) {
        let p = PathBuf::from(text.trim());
        if p.join(start_script()).is_file() {
            return Some(p);
        }
    }
    let built_from = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    built_from
        .join(start_script())
        .is_file()
        .then(|| built_from.canonicalize().unwrap_or(built_from))
}

/// `start` or `stop` – the command line of this OS's service script.
fn script_command(root: &std::path::Path, action: &str) -> Command {
    #[cfg(target_os = "macos")]
    {
        let name = if action == "start" { "start.sh" } else { "stop.sh" };
        let mut cmd = Command::new("/bin/bash");
        cmd.arg(root.join("scripts").join(name));
        if action == "start" {
            cmd.arg("--no-app");
        }
        // Apps started from Finder get a minimal PATH; the scripts extend it themselves.
        cmd.env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin");
        cmd
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let name = if action == "start" { "start.ps1" } else { "stop.ps1" };
        let mut cmd = Command::new("powershell.exe");
        cmd.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(root.join("scripts").join("windows").join(name));
        if action == "start" {
            cmd.arg("-NoApp");
        }
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let name = if action == "start" { "start.sh" } else { "stop.sh" };
        let mut cmd = Command::new("/bin/bash");
        cmd.arg(root.join("scripts").join("linux").join(name));
        if action == "start" {
            cmd.arg("--no-app");
        }
        cmd
    }
}

fn run_script(action: &str) -> Result<(), String> {
    let root = project_root().ok_or("Jarvis-Projektordner nicht gefunden")?;
    let logs = logs_dir();
    let _ = std::fs::create_dir_all(&logs);
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("launcher.log"))
        .map_err(|e| e.to_string())?;
    let err = log.try_clone().map_err(|e| e.to_string())?;
    let status = script_command(&root, action)
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(log)
        .stderr(err)
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(failure_message(action, &status.to_string(), &logs))
    }
}

#[cfg(target_os = "macos")]
fn failure_message(action: &str, status: &str, _logs: &std::path::Path) -> String {
    let name = if action == "start" { "start.sh" } else { "stop.sh" };
    format!("{name} beendet mit {status} (siehe ~/Library/Logs/Jarvis/launcher.log)")
}

#[cfg(not(target_os = "macos"))]
fn failure_message(action: &str, status: &str, logs: &std::path::Path) -> String {
    format!("{action} beendet mit {status} (siehe {})", logs.join("launcher.log").display())
}

/// Starts all services in the background and reports the outcome to the webview.
pub fn start_services(app: &AppHandle) {
    if core_client::is_remote() {
        let _ = app.emit("services", serde_json::json!({ "state": "running", "detail": "remote" }));
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        let _ = handle.emit("services", serde_json::json!({ "state": "starting" }));
        let outcome = run_script("start");
        println!("[jarvis-desktop] services start: {outcome:?}");
        let payload = match outcome {
            Ok(()) => serde_json::json!({ "state": "running" }),
            Err(e) => serde_json::json!({ "state": "error", "detail": e }),
        };
        let _ = handle.emit("services", payload);
    });
}

fn keep_flag() -> PathBuf {
    data_dir().join("keep-running")
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
    if core_client::is_remote() {
        return; // a client never stops the Core of another computer
    }
    let worker = std::thread::spawn(|| run_script("stop"));
    for _ in 0..100 {
        if worker.is_finished() {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}
