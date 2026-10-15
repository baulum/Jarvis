//! Minimal, dependency-free access to the local core for tray actions.
//!
//! Tray actions (kill switch, push-to-talk) must work even when the webview is hidden or
//! busy, so they are sent from Rust over a plain HTTP/1.1 request to the loopback core.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::time::Duration;

pub const CORE_ADDR: &str = "127.0.0.1:8765";

/// Location of the owner token written by the core installer (mode 0600).
fn token_path() -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("JARVIS_DESKTOP_TOKEN_FILE") {
        return Ok(PathBuf::from(p));
    }
    let home = std::env::var("HOME").map_err(|_| "HOME ist nicht gesetzt".to_string())?;
    Ok(PathBuf::from(home).join("Library/Application Support/Jarvis/desktop.token"))
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
    use super::parse_status;

    #[test]
    fn parses_status_line() {
        assert_eq!(parse_status(b"HTTP/1.1 200 OK\r\n"), Some(200));
        assert_eq!(parse_status(b"HTTP/1.1 403 Forbidden\r\n"), Some(403));
        assert_eq!(parse_status(b"garbage"), None);
    }
}
