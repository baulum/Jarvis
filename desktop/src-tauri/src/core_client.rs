//! Minimal, dependency-free access to the local core for tray actions, plus the client connection.
//!
//! Tray actions (kill switch, push-to-talk) must work even when the webview is hidden or
//! busy, so they are sent from Rust over a plain HTTP/1.1 request to the loopback core.
//!
//! Remote mode: a `client.json` next to the token (`{"core_url": "https://…", "token": "jdv_…"}`, written by the
//! installer or the app's connection form) makes the app a pure client of a Core elsewhere (over Tailscale).
//! Then no local services are started and tray actions are handed to the webview, which talks HTTPS.
//! Without `client.json` – always on the Mac – everything is local, exactly as before.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::time::Duration;

pub const CORE_ADDR: &str = "127.0.0.1:8765";

/// The Core's data directory on this OS (matches config/jarvis.yaml and config/platform/*.yaml).
pub fn data_dir() -> Result<PathBuf, String> {
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").map_err(|_| "HOME ist nicht gesetzt".to_string())?;
        Ok(PathBuf::from(home).join("Library/Application Support/Jarvis"))
    }
    #[cfg(target_os = "windows")]
    {
        let appdata = std::env::var("APPDATA").map_err(|_| "APPDATA ist nicht gesetzt".to_string())?;
        Ok(PathBuf::from(appdata).join("Jarvis"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
            if !xdg.is_empty() {
                return Ok(PathBuf::from(xdg).join("jarvis"));
            }
        }
        let home = std::env::var("HOME").map_err(|_| "HOME ist nicht gesetzt".to_string())?;
        Ok(PathBuf::from(home).join(".local/share/jarvis"))
    }
}

/// Location of the owner token written by the core installer (mode 0600).
fn token_path() -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("JARVIS_DESKTOP_TOKEN_FILE") {
        return Ok(PathBuf::from(p));
    }
    Ok(data_dir()?.join("desktop.token"))
}

pub fn read_token() -> Result<String, String> {
    let path = token_path()?;
    let raw = std::fs::read_to_string(&path)
        .map_err(|e| format!("Token-Datei nicht lesbar ({}): {e}", path.display()))?;
    let token = raw.trim().to_string();
    if token.is_empty() {
        return Err(format!("Token-Datei ist leer ({})", path.display()));
    }
    Ok(token)
}

fn client_path() -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("JARVIS_CLIENT_FILE") {
        return Ok(PathBuf::from(p));
    }
    Ok(data_dir()?.join("client.json"))
}

/// A remote Core this app is a client of, or `None` (local Core, the default).
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct RemoteCore {
    pub core_url: String,
    pub token: String,
}

pub fn parse_client(raw: &str) -> Result<RemoteCore, String> {
    let v: serde_json::Value = serde_json::from_str(raw).map_err(|e| format!("client.json ist kein JSON: {e}"))?;
    let url = v.get("core_url").and_then(|x| x.as_str()).unwrap_or("").trim().trim_end_matches('/');
    let token = v.get("token").and_then(|x| x.as_str()).unwrap_or("").trim();
    if !url.starts_with("https://") && !url.starts_with("http://127.0.0.1") && !url.starts_with("http://localhost") {
        return Err("core_url muss mit https:// beginnen (Tailscale-Adresse)".into());
    }
    if token.is_empty() {
        return Err("token fehlt in client.json".into());
    }
    Ok(RemoteCore { core_url: url.to_string(), token: token.to_string() })
}

pub fn remote_core() -> Option<RemoteCore> {
    let raw = std::fs::read_to_string(client_path().ok()?).ok()?;
    match parse_client(&raw) {
        Ok(c) => Some(c),
        Err(e) => {
            eprintln!("[jarvis-desktop] client.json ignoriert: {e}");
            None
        }
    }
}

pub fn is_remote() -> bool {
    remote_core().is_some()
}

pub fn save_remote(core_url: &str, token: &str) -> Result<(), String> {
    let body = serde_json::json!({ "core_url": core_url.trim().trim_end_matches('/'), "token": token.trim() });
    parse_client(&body.to_string())?;
    let path = client_path()?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, serde_json::to_vec_pretty(&body).map_err(|e| e.to_string())?)
        .map_err(|e| format!("client.json nicht schreibbar: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

pub fn clear_remote() -> Result<(), String> {
    match std::fs::remove_file(client_path()?) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

/// POSTs a JSON body to the core and returns the HTTP status code.
pub fn post_json(path: &str, body: &str) -> Result<u16, String> {
    let token = read_token()?;
    let addr: SocketAddr = CORE_ADDR.parse().map_err(|e| format!("{e}"))?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2))
        .map_err(|e| format!("Core nicht erreichbar: {e}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(8)))
        .map_err(|e| e.to_string())?;
    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: {CORE_ADDR}\r\nAuthorization: Bearer {token}\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("Senden fehlgeschlagen: {e}"))?;
    let mut head = [0u8; 64];
    let n = stream
        .read(&mut head)
        .map_err(|e| format!("Keine Antwort: {e}"))?;
    parse_status(&head[..n]).ok_or_else(|| "Ungültige HTTP-Antwort".to_string())
}

fn parse_status(head: &[u8]) -> Option<u16> {
    let line = std::str::from_utf8(head).ok()?.lines().next()?;
    line.split_whitespace().nth(1)?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::{parse_client, parse_status};

    #[test]
    fn parses_status_line() {
        assert_eq!(parse_status(b"HTTP/1.1 200 OK\r\n"), Some(200));
        assert_eq!(parse_status(b"HTTP/1.1 403 Forbidden\r\n"), Some(403));
        assert_eq!(parse_status(b"garbage"), None);
    }

    #[test]
    fn parses_client_config() {
        let c = parse_client(r#"{"core_url": "https://mac.tail1234.ts.net/", "token": " jdv_x "}"#).unwrap();
        assert_eq!(c.core_url, "https://mac.tail1234.ts.net");
        assert_eq!(c.token, "jdv_x");
        assert!(parse_client(r#"{"core_url": "http://192.168.1.2:8767", "token": "t"}"#).is_err());
        assert!(parse_client(r#"{"core_url": "https://x"}"#).is_err());
        assert!(parse_client("kein json").is_err());
    }
}
