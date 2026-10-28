"""Publishing connectors for Instagram, Facebook, TikTok and YouTube – official APIs only, all off by default.

Each platform is a normal Jarvis connector (Connectoren view, Keychain secrets, enable switch, health check). The
OAuth flow runs over the Core's loopback callback ``/api/social/oauth/<platform>/callback`` like the Google
connector; tokens go straight into the Keychain (``secret_store``), never into logs, errors or the database.
Instagram and Facebook share one Meta app and one Meta login.

The connectors have no agent tools of their own: publishing goes through ``social_publish_video`` (risk high →
approval) in ``jarvis.social.tools``. API details and prerequisites: docs/SOCIAL_MEDIA.md.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.secrets import secret_store
from jarvis.tools.executor import ToolUserError

PLATFORMS = ("instagram", "facebook", "tiktok", "youtube")
CONNECTOR_ID = {p: f"social_{p}" for p in PLATFORMS}
GRAPH_VERSION = "v24.0"
META_SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "read_insights",
    "business_management",
]
TIKTOK_SCOPES = ["user.info.basic", "video.publish", "video.list"]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


class PublishError(ToolUserError):
    pass


def _safe_error(platform: str, r: httpx.Response) -> PublishError:
    """API error without request, headers or tokens – only status and the API's own message."""
    msg = ""
    try:
        data = r.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("code") or "")
        elif isinstance(data, dict):
            msg = str(data.get("error_description") or data.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        msg = ""
    return PublishError(f"{platform}: HTTP {r.status_code}{' – ' + msg[:200] if msg else ''}")


class PlatformConnector(Connector):
    platform: str = ""
    client_secret: str = ""  # Keychain name of the app credentials (JSON with client_id/client_secret)
    token_secret: str = ""  # Keychain name of the OAuth token (JSON)

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._pending: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ OAuth
    def redirect_uri(self) -> str:
        srv = self.state.settings.server
        return f"http://127.0.0.1:{srv.port}/api/social/oauth/{self.platform}/callback"

    def _client(self) -> dict[str, str]:
        raw = self.secret(self.client_secret)
        if not raw:
            raise ToolUserError(f"{self.manifest.name}: App-Zugangsdaten fehlen ({self.client_secret}).")
        data = json.loads(raw)
        inner = data.get("installed") or data.get("web") or data
        return {"client_id": str(inner["client_id"]), "client_secret": str(inner.get("client_secret", ""))}

    def token(self) -> dict[str, Any] | None:
        raw = self.secret(self.token_secret)
        return json.loads(raw) if raw else None

    def save_token(self, data: dict[str, Any]) -> None:
        secret_store.set(self.token_secret, json.dumps(data))

    @property
    def connected(self) -> bool:
        return self.token() is not None

    def begin_oauth(self) -> str:
        raise NotImplementedError

    async def finish_oauth(self, state: str, code: str) -> None:
        raise NotImplementedError

    def _take_pending(self, state: str) -> dict[str, str]:
        pending = self._pending.pop(state, None)
        if pending is None or time.time() - float(pending["ts"]) > 900:
            raise ToolUserError("OAuth-Status ungültig oder abgelaufen – bitte erneut verbinden")
        return pending

    # ------------------------------------------------------------------ publishing
    async def publish(self, video: Path, caption: str, title: str) -> dict[str, Any]:
        raise NotImplementedError

    async def metrics(self, post_id: str) -> dict[str, Any]:
        return {}

    def public_status(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "connector": self.manifest.id,
            "enabled": self.enabled,
            "app_configured": secret_store.exists(self.client_secret),
            "connected": self.connected,
            "account": (self.settings.get("account_name") or ""),
        }


# --------------------------------------------------------------------------- Meta (Instagram + Facebook)
class _MetaBase(PlatformConnector):
    client_secret = "meta-app"  # noqa: S105 - Keychain account name
    token_secret = "meta-oauth-token"  # noqa: S105 - Keychain account name

    @property
    def graph(self) -> str:
        return f"https://graph.facebook.com/{self.settings.get('graph_version') or GRAPH_VERSION}"

    def begin_oauth(self) -> str:
        client = self._client()
        state = secrets.token_urlsafe(24)
        self._pending[state] = {"ts": str(time.time())}
        version = self.settings.get("graph_version") or GRAPH_VERSION
        return f"https://www.facebook.com/{version}/dialog/oauth?" + urllib.parse.urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": self.redirect_uri(),
                "state": state,
                "response_type": "code",
                "scope": ",".join(META_SCOPES),
            }
        )

    async def finish_oauth(self, state: str, code: str) -> None:
        self._take_pending(state)
        client = self._client()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.graph}/oauth/access_token",
                params={**client, "redirect_uri": self.redirect_uri(), "code": code},
            )
            if r.status_code >= 400:
                raise _safe_error("Meta", r)
            short = r.json()["access_token"]
            r = await c.get(
                f"{self.graph}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", **client, "fb_exchange_token": short},
            )
            if r.status_code >= 400:
                raise _safe_error("Meta", r)
            user_token = r.json()["access_token"]
            r = await c.get(
                f"{self.graph}/me/accounts",
                params={
                    "fields": "id,name,access_token,instagram_business_account{id,username}",
                    "access_token": user_token,
                },
            )
            if r.status_code >= 400:
                raise _safe_error("Meta", r)
        pages = r.json().get("data") or []
        wanted = str(self.settings.get("page_id") or "")
        page = next((p for p in pages if not wanted or p.get("id") == wanted), None)
        if page is None:
            raise ToolUserError(
                "Keine Facebook-Seite gefunden – das Konto braucht eine Seite (mit verknüpftem Instagram-Profil)."
            )
        ig = page.get("instagram_business_account") or {}
        self.save_token(
            {
                "user_token": user_token,
                "page_token": page["access_token"],
                "page_id": page["id"],
                "page_name": page.get("name", ""),
                "ig_user_id": ig.get("id"),
                "ig_username": ig.get("username"),
                "obtained_at": int(time.time()),
            }
        )

    def _tok(self) -> dict[str, Any]:
        tok = self.token()
        if not tok:
            raise ToolUserError("Meta ist nicht verbunden (Social Media › Connector › Verbinden).")
        return tok

    async def _poll(
        self, c: httpx.AsyncClient, url: str, params: dict[str, Any], field: str, done: set[str], fail: set[str]
    ) -> dict[str, Any]:
        for _ in range(60):
            r = await c.get(url, params=params)
            if r.status_code >= 400:
                raise _safe_error(self.manifest.name, r)
            data = r.json()
            status = str(data.get(field) or "")
            if isinstance(data.get(field), dict):
                status = str(data[field].get("video_status") or data[field].get("status") or "")
            if status in done:
                return dict(data)
            if status in fail:
                raise PublishError(f"{self.manifest.name}: Verarbeitung fehlgeschlagen ({status})")
            await asyncio.sleep(5)
        raise PublishError(f"{self.manifest.name}: Verarbeitung dauert zu lange")


class InstagramConnector(_MetaBase):
    platform = "instagram"
    manifest = Manifest(
        id="social_instagram",
        name="Instagram (Reels)",
        description="Reels über die Instagram Graph API veröffentlichen (Business-/Creator-Konto mit Facebook-Seite). "
        "Standardmäßig aus; Veröffentlichen immer mit Freigabe.",
        category="communication",
        auth="oauth2",
        secrets=[
            SecretField(
                "meta-app",
                "Meta-App (JSON: client_id, client_secret)",
                help="developers.facebook.com › Meine Apps › App-Einstellungen › Allgemein",
            ),
            SecretField("meta-oauth-token", "Autorisierung (wird durch „Mit Meta verbinden“ gesetzt)"),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "graph_version": {"type": "string"},
                "account_name": {"type": "string"},
            },
        },
        docs_url="https://developers.facebook.com/docs/instagram-platform/content-publishing",
        mcp_note="Kein offizieller MCP-Server; Graph API direkt (Content Publishing).",
    )

    async def health(self) -> Health:
        tok = self._tok()
        if not tok.get("ig_user_id"):
            return Health("error", "Mit der Facebook-Seite ist kein Instagram-Business-/Creator-Konto verknüpft")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{self.graph}/{tok['ig_user_id']}", params={"fields": "username", "access_token": tok["page_token"]}
            )
        if r.status_code >= 400:
            return Health("error", str(_safe_error("Instagram", r)))
        return Health("ok", f"Verbunden als @{r.json().get('username', '')}")

    async def publish(self, video: Path, caption: str, title: str) -> dict[str, Any]:
        tok = self._tok()
        ig, access = tok.get("ig_user_id"), tok["page_token"]
        if not ig:
            raise PublishError("Instagram: kein Business-/Creator-Konto mit der Seite verknüpft")
        size = video.stat().st_size
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{self.graph}/{ig}/media",
                data={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": access,
                },
            )
            if r.status_code >= 400:
                raise _safe_error("Instagram", r)
            container = r.json()["id"]
            version = self.settings.get("graph_version") or GRAPH_VERSION
            r = await c.post(
                f"https://rupload.facebook.com/ig-api-upload/{version}/{container}",
                headers={"Authorization": f"OAuth {access}", "offset": "0", "file_size": str(size)},
                content=video.read_bytes(),
            )
            if r.status_code >= 400:
                raise _safe_error("Instagram", r)
            await self._poll(
                c,
                f"{self.graph}/{container}",
                {"fields": "status_code", "access_token": access},
                "status_code",
                {"FINISHED"},
                {"ERROR", "EXPIRED"},
            )
            r = await c.post(
                f"{self.graph}/{ig}/media_publish", data={"creation_id": container, "access_token": access}
            )
            if r.status_code >= 400:
                raise _safe_error("Instagram", r)
            media_id = r.json()["id"]
            r = await c.get(f"{self.graph}/{media_id}", params={"fields": "permalink", "access_token": access})
            url = r.json().get("permalink") if r.status_code < 400 else None
        return {"post_id": media_id, "url": url}

    async def metrics(self, post_id: str) -> dict[str, Any]:
        tok = self._tok()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{self.graph}/{post_id}/insights",
                params={
                    "metric": "reach,views,likes,comments,shares,saved,total_interactions",
                    "access_token": tok["page_token"],
                },
            )
        if r.status_code >= 400:
            return {}
        return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in r.json().get("data") or []}


class FacebookConnector(_MetaBase):
    platform = "facebook"
    manifest = Manifest(
        id="social_facebook",
        name="Facebook (Reels der Seite)",
        description="Reels auf der Facebook-Seite von PJE Systems veröffentlichen (Graph API, Video Reels). "
        "Standardmäßig aus; Veröffentlichen immer mit Freigabe.",
        category="communication",
        auth="oauth2",
        secrets=[
            SecretField("meta-app", "Meta-App (JSON: client_id, client_secret)", help="dieselbe App wie bei Instagram"),
            SecretField("meta-oauth-token", "Autorisierung (wird durch „Mit Meta verbinden“ gesetzt)"),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "graph_version": {"type": "string"},
                "account_name": {"type": "string"},
            },
        },
        docs_url="https://developers.facebook.com/docs/video-api/guides/reels-publishing",
        mcp_note="Kein offizieller MCP-Server; Graph API direkt (Reels Publishing).",
    )

    async def health(self) -> Health:
        tok = self._tok()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{self.graph}/{tok['page_id']}", params={"fields": "name", "access_token": tok["page_token"]}
            )
        if r.status_code >= 400:
            return Health("error", str(_safe_error("Facebook", r)))
        return Health("ok", f"Seite: {r.json().get('name', '')}")

    async def publish(self, video: Path, caption: str, title: str) -> dict[str, Any]:
        tok = self._tok()
        page, access = tok["page_id"], tok["page_token"]
        size = video.stat().st_size
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.graph}/{page}/video_reels", data={"upload_phase": "start", "access_token": access})
            if r.status_code >= 400:
                raise _safe_error("Facebook", r)
            video_id = r.json()["video_id"]
            version = self.settings.get("graph_version") or GRAPH_VERSION
            r = await c.post(
                f"https://rupload.facebook.com/video-upload/{version}/{video_id}",
                headers={"Authorization": f"OAuth {access}", "offset": "0", "file_size": str(size)},
                content=video.read_bytes(),
            )
            if r.status_code >= 400:
                raise _safe_error("Facebook", r)
            r = await c.post(
                f"{self.graph}/{page}/video_reels",
                data={
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "video_state": "PUBLISHED",
                    "description": caption,
                    "access_token": access,
                },
            )
            if r.status_code >= 400:
                raise _safe_error("Facebook", r)
        return {"post_id": video_id, "url": f"https://www.facebook.com/reel/{video_id}"}

    async def metrics(self, post_id: str) -> dict[str, Any]:
        tok = self._tok()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{self.graph}/{post_id}/video_insights", params={"access_token": tok["page_token"]})
        if r.status_code >= 400:
            return {}
        return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in r.json().get("data") or []}


# --------------------------------------------------------------------------- TikTok
class TikTokConnector(PlatformConnector):
    platform = "tiktok"
    client_secret = "tiktok-app"  # noqa: S105 - Keychain account name
    token_secret = "tiktok-oauth-token"  # noqa: S105 - Keychain account name
    API = "https://open.tiktokapis.com/v2"
    manifest = Manifest(
        id="social_tiktok",
        name="TikTok (Content Posting API)",
        description="Videos über die offizielle Content Posting API hochladen. Ohne App-Audit durch TikTok nur als "
        "privater Post (SELF_ONLY). Standardmäßig aus; Veröffentlichen immer mit Freigabe.",
        category="communication",
        auth="oauth2",
        secrets=[
            SecretField(
                "tiktok-app",
                "TikTok-App (JSON: client_id = Client Key, client_secret)",
                help="developers.tiktok.com › Manage apps",
            ),
            SecretField("tiktok-oauth-token", "Autorisierung (wird durch „Mit TikTok verbinden“ gesetzt)"),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "privacy_level": {
                    "type": "string",
                    "enum": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"],
                },
                "audited": {"type": "boolean"},
                "account_name": {"type": "string"},
            },
        },
        docs_url="https://developers.tiktok.com/doc/content-posting-api-get-started",
        mcp_note="Kein offizieller MCP-Server; Content Posting API direkt.",
    )

    def begin_oauth(self) -> str:
        client = self._client()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok desktop PKCE: hex-encoded SHA-256
        self._pending[state] = {"verifier": verifier, "ts": str(time.time())}
        return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(
            {
                "client_key": client["client_id"],
                "scope": ",".join(TIKTOK_SCOPES),
                "response_type": "code",
                "redirect_uri": self.redirect_uri(),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    async def finish_oauth(self, state: str, code: str) -> None:
        pending = self._take_pending(state)
        client = self._client()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self.API}/oauth/token/",
                data={
                    "client_key": client["client_id"],
                    "client_secret": client["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri(),
                    "code_verifier": pending["verifier"],
                },
            )
        if r.status_code >= 400 or "access_token" not in r.json():
            raise _safe_error("TikTok", r)
        tok = r.json()
        self.save_token({**tok, "expires_at": int(time.time()) + int(tok.get("expires_in", 86400)) - 60})

    async def _access(self) -> str:
        tok = self.token()
        if not tok:
            raise ToolUserError("TikTok ist nicht verbunden (Social Media › Connector › Verbinden).")
        if tok.get("expires_at", 0) > time.time():
            return str(tok["access_token"])
        client = self._client()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self.API}/oauth/token/",
                data={
                    "client_key": client["client_id"],
                    "client_secret": client["client_secret"],
                    "grant_type": "refresh_token",
                    "refresh_token": tok["refresh_token"],
                },
            )
        if r.status_code >= 400 or "access_token" not in r.json():
            raise ToolUserError("TikTok-Autorisierung abgelaufen – bitte neu verbinden.")
        new = r.json()
        self.save_token({**tok, **new, "expires_at": int(time.time()) + int(new.get("expires_in", 86400)) - 60})
        return str(new["access_token"])

    async def health(self) -> Health:
        access = await self._access()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{self.API}/post/publish/creator_info/query/", headers={"Authorization": f"Bearer {access}"}
            )
        if r.status_code >= 400:
            return Health("error", str(_safe_error("TikTok", r)))
        info = r.json().get("data") or {}
        note = "" if self.settings.get("audited") else " – ohne App-Audit nur private Posts"
        return Health("ok", f"Verbunden als {info.get('creator_nickname', '')}{note}")

    async def publish(self, video: Path, caption: str, title: str) -> dict[str, Any]:
        access = await self._access()
        size = video.stat().st_size
        chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
        chunks = max(1, math.floor(size / chunk))
        privacy = self.settings.get("privacy_level") if self.settings.get("audited") else "SELF_ONLY"
        headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8"}
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{self.API}/post/publish/video/init/",
                headers=headers,
                json={
                    "post_info": {
                        "title": caption[:2200],
                        "privacy_level": privacy or "SELF_ONLY",
                        "disable_comment": False,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": size,
                        "chunk_size": chunk,
                        "total_chunk_count": chunks,
                    },
                },
            )
            if r.status_code >= 400:
                raise _safe_error("TikTok", r)
            data = r.json()["data"]
            publish_id, upload_url = data["publish_id"], data["upload_url"]
            raw = video.read_bytes()
            for i in range(chunks):
                start = i * chunk
                end = size if i == chunks - 1 else start + chunk
                r = await c.put(
                    upload_url,
                    headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{end - 1}/{size}"},
                    content=raw[start:end],
                )
                if r.status_code >= 400:
                    raise _safe_error("TikTok", r)
            for _ in range(60):
                r = await c.post(
                    f"{self.API}/post/publish/status/fetch/", headers=headers, json={"publish_id": publish_id}
                )
                if r.status_code >= 400:
                    raise _safe_error("TikTok", r)
                st = r.json().get("data") or {}
                if st.get("status") == "PUBLISH_COMPLETE":
                    ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                    post = str(ids[0]) if ids else publish_id
                    return {"post_id": post, "url": None, "privacy": privacy}
                if st.get("status") == "FAILED":
                    raise PublishError(f"TikTok: {st.get('fail_reason', 'Veröffentlichung fehlgeschlagen')}")
                await asyncio.sleep(5)
        raise PublishError("TikTok: Verarbeitung dauert zu lange")


# --------------------------------------------------------------------------- YouTube
class YouTubeConnector(PlatformConnector):
    platform = "youtube"
    client_secret = "google-oauth-client"  # noqa: S105 - the Google connector's OAuth client
    token_secret = "youtube-oauth-token"  # noqa: S105 - Keychain account name
    manifest = Manifest(
        id="social_youtube",
        name="YouTube (Shorts)",
        description="Shorts über die YouTube Data API v3 hochladen (videos.insert, 1 600 Quota-Einheiten pro Upload). "
        "Ungeprüfte API-Projekte laden nur privat hoch. Standardmäßig aus; Veröffentlichen immer mit Freigabe.",
        category="communication",
        auth="oauth2",
        secrets=[
            SecretField(
                "google-oauth-client", "Google-OAuth-Client (wie beim Google-Connector; YouTube Data API v3 aktivieren)"
            ),
            SecretField("youtube-oauth-token", "Autorisierung (wird durch „Mit YouTube verbinden“ gesetzt)"),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "privacy_status": {"type": "string", "enum": ["private", "unlisted", "public"]},
                "category_id": {"type": "string"},
                "account_name": {"type": "string"},
            },
        },
        docs_url="https://developers.google.com/youtube/v3/docs/videos/insert",
        mcp_note="Kein offizieller MCP-Server; YouTube Data API v3 direkt.",
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._access: tuple[str, float] | None = None

    def begin_oauth(self) -> str:
        client = self._client()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self._pending[state] = {"verifier": verifier, "ts": str(time.time())}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": self.redirect_uri(),
                "response_type": "code",
                "scope": " ".join(YOUTUBE_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    async def finish_oauth(self, state: str, code: str) -> None:
        pending = self._take_pending(state)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://oauth2.googleapis.com/token",
                data={
                    **self._client(),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri(),
                    "code_verifier": pending["verifier"],
                },
            )
        if r.status_code >= 400 or "refresh_token" not in r.json():
            raise ToolUserError(
                "YouTube hat kein Refresh-Token geliefert – Zugriff im Google-Konto entfernen und neu verbinden."
            )
        tok = r.json()
        self.save_token({"refresh_token": tok["refresh_token"]})
        self._access = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)

    async def _token(self) -> str:
        if self._access and self._access[1] > time.time():
            return self._access[0]
        tok = self.token()
        if not tok:
            raise ToolUserError("YouTube ist nicht verbunden (Social Media › Connector › Verbinden).")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://oauth2.googleapis.com/token",
                data={**self._client(), "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"},
            )
        if r.status_code >= 400:
            raise ToolUserError("YouTube-Autorisierung abgelaufen – bitte neu verbinden.")
        data = r.json()
        self._access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60)
        return self._access[0]

    async def health(self) -> Health:
        token = await self._token()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code >= 400:
            return Health("error", str(_safe_error("YouTube", r)))
        items = r.json().get("items") or []
        return Health("ok", f"Kanal: {items[0]['snippet']['title']}" if items else "Kein Kanal im Konto")

    async def publish(self, video: Path, caption: str, title: str) -> dict[str, Any]:
        token = await self._token()
        meta = {
            "snippet": {
                "title": title[:100],
                "description": caption[:4900],
                "categoryId": self.settings.get("category_id") or "22",
                "defaultLanguage": "de",
            },
            "status": {
                "privacyStatus": self.settings.get("privacy_status") or "private",
                "selfDeclaredMadeForKids": False,
            },
        }
        size = video.stat().st_size
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(size),
                },
                json=meta,
            )
            if r.status_code >= 400 or "location" not in r.headers:
                raise _safe_error("YouTube", r)
            r = await c.put(r.headers["location"], headers={"Content-Type": "video/mp4"}, content=video.read_bytes())
            if r.status_code >= 400:
                raise _safe_error("YouTube", r)
        vid = r.json()["id"]
        return {"post_id": vid, "url": f"https://www.youtube.com/shorts/{vid}"}

    async def metrics(self, post_id: str) -> dict[str, Any]:
        token = await self._token()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics", "id": post_id},
                headers={"Authorization": f"Bearer {token}"},
            )
        items = r.json().get("items") or [] if r.status_code < 400 else []
        return dict(items[0].get("statistics") or {}) if items else {}


PLATFORM_CONNECTORS: tuple[type[PlatformConnector], ...] = (
    InstagramConnector,
    FacebookConnector,
    TikTokConnector,
    YouTubeConnector,
)
