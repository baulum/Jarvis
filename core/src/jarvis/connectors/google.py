"""Google Workspace: Gmail, Calendar, Drive via the official REST APIs (OAuth 2.0, PKCE).

Google's official Workspace MCP server is (as of 2026-05) only a public developer preview;
Jarvis therefore talks to the stable REST APIs directly with a narrow scope set. The OAuth
client (type "Desktop app") is created by Sir in Google Cloud; the refresh token is stored
in the Keychain. The redirect goes to the local core (loopback flow).
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from jarvis.agent.persona import TZ
from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.secrets import secret_store
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL, not a secret

# Attachments may only come from these folders (checked after resolving symlinks and "..").
ATTACHMENT_ROOTS = (
    Path("/Users/paul/Developer/Projects"),
    Path("/Users/paul/Developer/Jarvis-Workspaces"),
)
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 10
_BLOCKED_PARTS = {".git", ".ssh", ".env", ".aws", ".gnupg"}


def load_attachments(
    paths: list[str],
    roots: tuple[Path, ...] = ATTACHMENT_ROOTS,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> list[tuple[str, str, bytes]]:
    """Validate attachment paths and return (filename, mime type, data) tuples."""
    if not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths):
        raise ToolUserError("attachments muss eine Liste von Dateipfaden sein.")
    if len(paths) > MAX_ATTACHMENTS:
        raise ToolUserError(f"Zu viele Anhänge (maximal {MAX_ATTACHMENTS}).")
    allowed = [r.resolve() for r in roots]
    limit_mb = max_bytes // (1024 * 1024)
    files: list[Path] = []
    total = 0
    for raw in paths:
        try:
            p = Path(raw).expanduser().resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise ToolUserError(f"Anhang nicht gefunden: {raw}") from None
        except (OSError, RuntimeError):
            raise ToolUserError(f"Anhang nicht lesbar: {raw}") from None
        if not any(p.is_relative_to(r) for r in allowed):
            raise ToolUserError(f"Anhang nicht erlaubt (Pfad außerhalb der freigegebenen Ordner): {raw}")
        if _BLOCKED_PARTS & {part.lower() for part in p.parts} or p.name.lower().startswith(".env"):
            raise ToolUserError(f"Anhang nicht erlaubt (sensible Datei): {raw}")
        if not p.is_file():
            raise ToolUserError(f"Anhang ist keine reguläre Datei: {raw}")
        total += p.stat().st_size
        if total > max_bytes:
            raise ToolUserError(f"Anhänge zu groß (Gesamtgrenze {limit_mb} MB).")
        files.append(p)
    out: list[tuple[str, str, bytes]] = []
    total = 0
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            raise ToolUserError(f"Anhang nicht lesbar: {p.name}") from None
        total += len(data)
        if total > max_bytes:
            raise ToolUserError(f"Anhänge zu groß (Gesamtgrenze {limit_mb} MB).")
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out.append((p.name, mime, data))
    return out


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class GoogleConnector(Connector):
    manifest = Manifest(
        id="google",
        name="Google (Gmail, Kalender, Drive)",
        description="E-Mails suchen, lesen, Entwürfe und Versand (nach Freigabe); Termine lesen und anlegen; "
        "Drive-Dateien suchen und lesen.",
        category="productivity",
        auth="oauth2",
        secrets=[
            SecretField(
                "google-oauth-client",
                "OAuth-Client (JSON, Typ 'Desktop-App')",
                help="Google Cloud Console › APIs & Dienste › Anmeldedaten › OAuth-Client-ID (Desktop-App). "
                "Gmail-, Calendar- und Drive-API aktivieren. JSON-Inhalt hier einfügen.",
            ),
            SecretField("google-oauth-token", "Autorisierung (wird durch 'Mit Google verbinden' gesetzt)"),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "account_email": {"type": "string"},
            },
        },
        mcp_note="Offizieller Google-Workspace-MCP-Server ist erst Developer Preview (05/2026); Jarvis nutzt die "
        "stabilen REST-APIs mit minimalen Scopes.",
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._access: tuple[str, float] | None = None
        self._pending: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ OAuth
    def _client(self) -> dict[str, str]:
        raw = self.secret("google-oauth-client")
        if not raw:
            raise ToolUserError("Google-OAuth-Client fehlt.")
        data = json.loads(raw)
        inner = data.get("installed") or data.get("web") or data
        return {"client_id": inner["client_id"], "client_secret": inner.get("client_secret", "")}

    def redirect_uri(self) -> str:
        srv = self.state.settings.server
        return f"http://127.0.0.1:{srv.port}/api/connectors/google/oauth/callback"

    def begin_oauth(self) -> str:
        client = self._client()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self._pending[state] = {"verifier": verifier, "ts": str(time.time())}
        return (
            AUTH_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": client["client_id"],
                    "redirect_uri": self.redirect_uri(),
                    "response_type": "code",
                    "scope": " ".join(SCOPES),
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def finish_oauth(self, state: str, code: str) -> None:
        pending = self._pending.pop(state, None)
        if pending is None or time.time() - float(pending["ts"]) > 900:
            raise ToolUserError("OAuth-Status ungültig oder abgelaufen")
        client = self._client()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                TOKEN_URL,
                data={
                    **client,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri(),
                    "code_verifier": pending["verifier"],
                },
            )
        if r.status_code >= 400:
            raise ToolUserError(f"Token-Austausch fehlgeschlagen: {r.text[:200]}")
        tok = r.json()
        if "refresh_token" not in tok:
            raise ToolUserError(
                "Google hat kein Refresh-Token geliefert (Zugriff in Google-Konto entfernen und erneut verbinden)."
            )
        secret_store.set("google-oauth-token", json.dumps({"refresh_token": tok["refresh_token"]}))
        self._access = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)

    async def _token(self) -> str:
        if self._access and self._access[1] > time.time():
            return self._access[0]
        raw = self.secret("google-oauth-token")
        if not raw:
            raise ToolUserError("Google ist nicht verbunden (OAuth fehlt).")
        refresh = json.loads(raw)["refresh_token"]
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                TOKEN_URL, data={**self._client(), "refresh_token": refresh, "grant_type": "refresh_token"}
            )
        if r.status_code >= 400:
            raise ToolUserError("Google-Autorisierung abgelaufen oder widerrufen – bitte neu verbinden.")
        tok = r.json()
        self._access = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
        return self._access[0]

    async def _api(self, method: str, url: str, **kw: Any) -> Any:
        token = await self._token()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
        if r.status_code >= 400:
            raise ToolUserError(f"Google-API-Fehler {r.status_code}: {r.text[:300]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content

    async def health(self) -> Health:
        try:
            prof = await self._api("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
            return Health("ok", f"Verbunden als {prof.get('emailAddress')}")
        except ToolUserError as e:
            return Health("error", str(e))

    # ------------------------------------------------------------------ helpers
    async def send_mail(
        self,
        to: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, str, bytes]] | None = None,
        draft: bool = False,
    ) -> Any:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        for name, mime, data in attachments or []:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        if draft:
            return await self._api(
                "POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts", json={"message": {"raw": raw}}
            )
        return await self._api(
            "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": raw}
        )

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[ToolSpec]:
        G = "https://gmail.googleapis.com/gmail/v1/users/me"
        C = "https://www.googleapis.com/calendar/v3"
        D = "https://www.googleapis.com/drive/v3"
        S = "google"

        async def gmail_search(ctx: ToolContext, a: dict[str, Any]) -> Any:
            res = await self._api(
                "GET", f"{G}/messages", params={"q": a["query"], "maxResults": min(int(a.get("limit", 10)), 20)}
            )
            out = []
            for m in res.get("messages", []):
                meta = await self._api(
                    "GET",
                    f"{G}/messages/{m['id']}",
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                )
                hdr = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
                out.append(
                    {
                        "id": m["id"],
                        "from": hdr.get("From"),
                        "subject": hdr.get("Subject"),
                        "date": hdr.get("Date"),
                        "snippet": meta.get("snippet", ""),
                    }
                )
            listing = "\n".join(
                f"- [{o['id']}] {o['date']} | {o['from']} | {o['subject']} | {o['snippet']}" for o in out
            )
            return {"count": len(out), **wrap_untrusted(listing or "(keine Treffer)", "gmail", 8000)}

        async def gmail_read(ctx: ToolContext, a: dict[str, Any]) -> Any:
            m = await self._api("GET", f"{G}/messages/{a['message_id']}", params={"format": "full"})
            hdr = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            body = _extract_text(m.get("payload", {}))
            return {
                "id": m["id"],
                "from": hdr.get("From"),
                "to": hdr.get("To"),
                "subject": hdr.get("Subject"),
                "date": hdr.get("Date"),
                **wrap_untrusted(body, f"email von {hdr.get('From')}", 10000),
            }

        async def gmail_draft(ctx: ToolContext, a: dict[str, Any]) -> Any:
            res = await self.send_mail(a["to"], a["subject"], a["body"], draft=True)
            return {"draft_id": res.get("id")}

        async def gmail_send(ctx: ToolContext, a: dict[str, Any]) -> Any:
            files = load_attachments(a["attachments"]) if a.get("attachments") else None
            res = await self.send_mail(a["to"], a["subject"], a["body"], attachments=files)
            out: dict[str, Any] = {"message_id": res.get("id"), "sent": True}
            if files:
                out["attachments"] = [name for name, _, _ in files]
            return out

        async def cal_list(ctx: ToolContext, a: dict[str, Any]) -> Any:
            start = a.get("from") or datetime.now(TZ).isoformat()
            end = a.get("to") or (datetime.now(TZ) + timedelta(days=int(a.get("days", 7)))).isoformat()
            cal = urllib.parse.quote(self.settings.get("calendar_id", "primary"))
            res = await self._api(
                "GET",
                f"{C}/calendars/{cal}/events",
                params={
                    "timeMin": _rfc3339(start),
                    "timeMax": _rfc3339(end),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 50,
                },
            )
            return [
                {
                    "id": e.get("id"),
                    "title": e.get("summary"),
                    "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
                    "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
                    "location": e.get("location"),
                }
                for e in res.get("items", [])
            ]

        async def cal_create(ctx: ToolContext, a: dict[str, Any], invite: bool = False) -> Any:
            start = datetime.fromisoformat(a["start"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=TZ)
            end = (
                datetime.fromisoformat(a["end"])
                if a.get("end")
                else start + timedelta(minutes=int(a.get("duration_minutes", 60)))
            )
            if end.tzinfo is None:
                end = end.replace(tzinfo=TZ)
            body: dict[str, Any] = {
                "summary": a["title"],
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            }
            for k in ("location", "description"):
                if a.get(k):
                    body[k] = a[k]
            params = {}
            if invite:
                body["attendees"] = [{"email": e} for e in a["attendees"]]
                params["sendUpdates"] = "all"
            cal = urllib.parse.quote(self.settings.get("calendar_id", "primary"))
            res = await self._api("POST", f"{C}/calendars/{cal}/events", json=body, params=params)
            return {"event_id": res.get("id"), "link": res.get("htmlLink"), "start": start.isoformat()}

        async def cal_invite(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await cal_create(ctx, a, invite=True)

        async def drive_search(ctx: ToolContext, a: dict[str, Any]) -> Any:
            q = a["query"].replace("'", "\\'")
            res = await self._api(
                "GET",
                f"{D}/files",
                params={
                    "q": f"fullText contains '{q}' and trashed = false",
                    "pageSize": min(int(a.get("limit", 10)), 25),
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                },
            )
            return res.get("files", [])

        async def drive_read(ctx: ToolContext, a: dict[str, Any]) -> Any:
            meta = await self._api("GET", f"{D}/files/{a['file_id']}", params={"fields": "id,name,mimeType"})
            mime = meta.get("mimeType", "")
            if mime.startswith("application/vnd.google-apps."):
                export = {"document": "text/plain", "spreadsheet": "text/csv", "presentation": "text/plain"}.get(
                    mime.rsplit(".", 1)[-1]
                )
                if not export:
                    raise ToolUserError(f"Dateityp {mime} kann nicht als Text gelesen werden")
                data = await self._api("GET", f"{D}/files/{a['file_id']}/export", params={"mimeType": export})
            elif mime.startswith("text/") or mime in ("application/json",):
                data = await self._api("GET", f"{D}/files/{a['file_id']}", params={"alt": "media"})
            else:
                raise ToolUserError(f"Nur Text- und Google-Dokumente werden gelesen (Typ: {mime})")
            text = data.decode(errors="replace") if isinstance(data, bytes) else json.dumps(data)
            return {"name": meta.get("name"), **wrap_untrusted(text, f"drive:{meta.get('name')}", 12000)}

        ev_props = {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "ISO-8601"},
            "end": {"type": "string"},
            "duration_minutes": {"type": "integer", "minimum": 5},
            "location": {"type": "string"},
            "description": {"type": "string"},
        }
        mail_props = {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}
        send_props = {
            **mail_props,
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: absolute Dateipfade als Anhänge. Nur Dateien aus den freigegebenen "
                "Ordnern (Projects, Jarvis-Workspaces), insgesamt max. 20 MB.",
            },
        }
        return [
            ToolSpec(
                "gmail_search",
                "Sucht E-Mails (Gmail-Suchsyntax, z. B. 'from:mueller newer_than:7d').",
                _obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                gmail_search,
                "read",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "gmail_read",
                "Liest eine E-Mail vollständig.",
                _obj({"message_id": {"type": "string"}}, ["message_id"]),
                gmail_read,
                "read",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "gmail_create_draft",
                "Legt einen E-Mail-Entwurf an (wird nicht gesendet).",
                _obj(mail_props, ["to", "subject", "body"]),
                gmail_draft,
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"E-Mail-Entwurf an {a['to']}: {a['subject']}",
            ),
            ToolSpec(
                "gmail_send",
                "Sendet eine E-Mail, optional mit Dateianhängen (attachments: Liste von Dateipfaden).",
                _obj(send_props, ["to", "subject", "body"]),
                gmail_send,
                "high",
                S,
                writes=True,
                summarize=lambda a: f"E-Mail senden an {a['to']}: {a['subject']} – {a['body'][:120]}"
                + (f" [Anhänge: {', '.join(a['attachments'])}]" if a.get("attachments") else ""),
            ),
            ToolSpec(
                "calendar_list_events",
                "Listet Termine (Standard: nächste 7 Tage).",
                _obj({"from": {"type": "string"}, "to": {"type": "string"}, "days": {"type": "integer"}}),
                cal_list,
                "read",
                S,
            ),
            ToolSpec(
                "calendar_create_event",
                "Legt einen Termin ohne Teilnehmer an.",
                _obj(ev_props, ["title", "start"]),
                cal_create,
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Termin '{a['title']}' am {a['start']}",
            ),
            ToolSpec(
                "calendar_invite",
                "Legt einen Termin an und lädt Teilnehmer per E-Mail ein.",
                _obj(
                    {**ev_props, "attendees": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                    ["title", "start", "attendees"],
                ),
                cal_invite,
                "high",
                S,
                writes=True,
                summarize=lambda a: f"Einladung '{a['title']}' am {a['start']} an {', '.join(a['attendees'])}",
            ),
            ToolSpec(
                "drive_search",
                "Sucht Dateien in Google Drive (Volltext).",
                _obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                drive_search,
                "read",
                S,
            ),
            ToolSpec(
                "drive_read",
                "Liest den Textinhalt einer Drive-Datei.",
                _obj({"file_id": {"type": "string"}}, ["file_id"]),
                drive_read,
                "read",
                S,
                untrusted_output=True,
            ),
        ]


def _rfc3339(value: str) -> str:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.isoformat()


def _extract_text(payload: dict[str, Any]) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode(errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_text(part)
        if text:
            return text
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        import re

        html = base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode(errors="replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""
