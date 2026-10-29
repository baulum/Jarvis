"""Connector ``uptik`` and its agent tools. Everything with an effect outside Jarvis runs through the ToolExecutor
(approval mode, risk, kill switch, idempotency ledger, audit):

* ``uptik_publish_now`` / ``uptik_schedule_post`` – risk *high*: public posts. ``prepare`` resolves video and
  accounts and validates per platform *before* the approval, so the approval shows exactly what will be posted.
* ``uptik_connect_account`` – risk *high*: opens the platform's login page in a visible browser; Sir logs in himself.
* ``uptik_cancel_post`` – risk *low*: only stops something not yet published.
* reading tools (status, accounts, queue, history, validate) – risk *read*.

Resuming a stopped account (circuit breaker) is deliberately no agent tool: only Sir, in the app, after checking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec
from jarvis.uptik.model import CONNECTOR_ID, FINISHED_STATUSES, OPEN_STATUSES, PLATFORM_LABEL, PLATFORMS
from jarvis.uptik.validation import options_schema

if TYPE_CHECKING:
    from jarvis.uptik.service import UptikService


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


_TAGS = {
    "oneOf": [
        {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 30},
        {"type": "string", "maxLength": 3000},
    ]
}
_CONTENT = {
    "caption": {"type": "string", "maxLength": 5000},
    "hashtags": _TAGS,
    "title": {"type": "string", "maxLength": 100},
}


def post_schema(scheduled: bool) -> dict[str, Any]:
    props: dict[str, Any] = {
        "video": {
            "type": "string",
            "minLength": 3,
            "maxLength": 2048,
            "description": "„inbox:<id>“ (Eingang), „upload:<name>“ oder absoluter Pfad auf dem Core-Rechner",
        },
        **_CONTENT,
        "accounts": {
            "type": "array",
            "items": {"type": "string", "maxLength": 80},
            "maxItems": 12,
            "description": "Konto-IDs oder Kontonamen",
        },
        "platforms": {
            "type": "array",
            "items": {"enum": list(PLATFORMS)},
            "maxItems": 3,
            "description": "statt accounts: das eine verbundene Konto je Plattform",
        },
        "per_platform": {
            "type": "object",
            "properties": {p: _obj(dict(_CONTENT)) for p in PLATFORMS},
            "additionalProperties": False,
        },
        "options": options_schema(),
        "source": {"type": "string", "maxLength": 40},
        "source_ref": {"type": "string", "maxLength": 200},
        # filled in by prepare (shown in the approval, executed as shown)
        "video_name": {"type": "string"},
        "video_size": {"type": "integer"},
        "video_sha256": {"type": "string"},
        "video_duration": {"type": ["number", "null"]},
        "targets": {"type": "array", "items": {"type": "object"}},
        "publish_at_utc": {"type": "string"},
        "publish_at_label": {"type": "string"},
    }
    req = ["video"]
    if scheduled:
        props["publish_at"] = {
            "type": "string",
            "maxLength": 40,
            "description": "„JJJJ-MM-TT HH:MM“ (Berliner Zeit) oder ISO 8601 mit Zeitzone",
        }
        req.append("publish_at")
    return _obj(props, req)


def _targets(a: dict[str, Any]) -> str:
    t = a.get("targets") or []
    if t:
        return ", ".join(f"{PLATFORM_LABEL.get(x['platform'], x['platform'])} ({x['label']})" for x in t)
    return ", ".join(a.get("accounts") or [PLATFORM_LABEL.get(p, p) for p in a.get("platforms") or []]) or "?"


def _post_summary(a: dict[str, Any], scheduled: bool) -> str:
    when = f" am {a.get('publish_at_label') or a.get('publish_at')}" if scheduled else " sofort"
    name = a.get("video_name") or str(a.get("video", ""))[-60:]
    cap = str(a.get("caption") or "").strip().replace("\n", " ")
    return f"Video „{name}“{when} öffentlich veröffentlichen auf {_targets(a)}" + (
        f" – Text: {cap[:160]}" if cap else ""
    )


def _audit_summary(a: dict[str, Any], scheduled: bool) -> str:
    return f"uptik {'schedule' if scheduled else 'publish'}: {a.get('video_name') or '?'} → {_targets(a)}"


def post_view(p: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "group_id",
        "account_id",
        "platform",
        "video_name",
        "caption",
        "hashtags",
        "title",
        "options",
        "run_at",
        "scheduled",
        "status",
        "stage",
        "attempts",
        "max_attempts",
        "next_attempt_at",
        "error_class",
        "last_error",
        "result_url",
        "source",
        "approval_id",
        "created_at",
        "updated_at",
        "finished_at",
        "video_size",
        "video_duration",
    )
    return {k: p.get(k) for k in keep}


def uptik_tools(svc: UptikService) -> list[ToolSpec]:
    C = CONNECTOR_ID

    async def status(ctx: ToolContext, a: dict[str, Any]) -> Any:
        st = await svc.status()
        st.pop("settings", None)
        return st

    async def list_accounts(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return {"accounts": await svc.accounts()}

    async def connect(ctx: ToolContext, a: dict[str, Any]) -> Any:
        aid = a.get("account_id")
        if not aid:
            if not a.get("platform") or not a.get("label"):
                raise ToolUserError("account_id oder platform + label angeben")
            aid = (
                await svc.create_account(
                    a["platform"], a["label"], a.get("runner"), a.get("device"), actor=ctx.principal.label
                )
            )["id"]
        return await svc.start_login(aid)

    async def prep_now(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
        return await svc.prepare(a, scheduled=False)

    async def prep_later(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
        return await svc.prepare(a, scheduled=True)

    async def publish_now(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.enqueue(a, ctx, scheduled=False)

    async def schedule(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.enqueue(a, ctx, scheduled=True)

    async def validate_post(ctx: ToolContext, a: dict[str, Any]) -> Any:
        try:
            out = await svc.prepare(a, scheduled=bool(a.get("publish_at")))
        except ToolUserError as e:
            return {"ok": False, "problems": str(e)}
        return {
            "ok": True,
            "targets": out["targets"],
            "video": {k: out[k] for k in ("video_name", "video_size", "video_duration")},
        }

    async def queue(ctx: ToolContext, a: dict[str, Any]) -> Any:
        await svc.ensure_open()
        return {"posts": [post_view(p) for p in await svc.store.posts(OPEN_STATUSES, limit=int(a.get("limit") or 50))]}

    async def cancel(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.cancel(a["post_id"], ctx.principal.label)

    async def history(ctx: ToolContext, a: dict[str, Any]) -> Any:
        await svc.ensure_open()
        rows = await svc.store.posts(FINISHED_STATUSES, limit=int(a.get("limit") or 30), newest_first=True)
        return {"posts": [post_view(p) for p in rows]}

    validate_schema = post_schema(False)
    validate_schema["properties"]["publish_at"] = {"type": "string", "maxLength": 40}
    return [
        ToolSpec(
            "uptik_status",
            "Status des UpTik-Connectors: Konten, angehaltene Konten (Circuit-Breaker), "
            "Warteschlange, Browser-Verfügbarkeit.",
            _obj({}),
            status,
            "read",
            C,
        ),
        ToolSpec(
            "uptik_list_accounts",
            "Listet die Social-Media-Konten (TikTok, Instagram Reels, YouTube Shorts) "
            "mit Verbindungsstatus und Autopost-Schalter.",
            _obj({}),
            list_accounts,
            "read",
            C,
        ),
        ToolSpec(
            "uptik_connect_account",
            "Startet die Anmeldung eines Kontos: Ein sichtbares Browserfenster öffnet die Anmeldeseite der Plattform, "
            "Sir meldet sich selbst an (Jarvis sieht kein Passwort). Liefert nur den Status zurück. Neues Konto: "
            "platform + label, bestehendes: account_id.",
            _obj(
                {
                    "account_id": {"type": "string", "maxLength": 64},
                    "platform": {"enum": list(PLATFORMS)},
                    "label": {"type": "string", "maxLength": 80},
                    "runner": {"enum": ["local", "device"]},
                    "device": {"type": "string", "maxLength": 120},
                }
            ),
            connect,
            "high",
            C,
            writes=True,
            timeout_s=60,
            summarize=lambda a: (
                "Social-Media-Konto anmelden: "
                + (a.get("account_id") or f"{PLATFORM_LABEL.get(a.get('platform', ''), '?')} „{a.get('label', '')}“")
            ),
        ),
        ToolSpec(
            "uptik_publish_now",
            "Veröffentlicht ein Video sofort auf einem oder mehreren Konten (TikTok, Instagram Reels, YouTube Shorts) "
            "über den Browser. Caption, Hashtags, Titel (YouTube) und Plattform-Einstellungen; per_platform "
            "überschreibt Texte je Plattform. Öffentlich sichtbar – braucht Sirs Freigabe.",
            post_schema(False),
            publish_now,
            "high",
            C,
            writes=True,
            timeout_s=300,
            prepare=prep_now,
            summarize=lambda a: _post_summary(a, False),
            audit_summary=lambda a: _audit_summary(a, False),
            private_args=("caption",),
        ),
        ToolSpec(
            "uptik_schedule_post",
            "Plant die Veröffentlichung eines Videos zu einem Zeitpunkt (Berliner Zeit) auf einem oder mehreren "
            "Konten. Jarvis veröffentlicht dann selbst (der Rechner mit dem Browser muss an sein). Braucht Freigabe.",
            post_schema(True),
            schedule,
            "high",
            C,
            writes=True,
            timeout_s=300,
            prepare=prep_later,
            summarize=lambda a: _post_summary(a, True),
            audit_summary=lambda a: _audit_summary(a, True),
            private_args=("caption",),
        ),
        ToolSpec(
            "uptik_validate_post",
            "Prüft einen Beitrag (Video, Konten, Texte, Zeitpunkt) ohne ihn einzureihen.",
            validate_schema,
            validate_post,
            "read",
            C,
            timeout_s=300,
        ),
        ToolSpec(
            "uptik_list_queue",
            "Wartende, laufende und angehaltene Beiträge der UpTik-Warteschlange.",
            _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
            queue,
            "read",
            C,
        ),
        ToolSpec(
            "uptik_cancel_post",
            "Bricht einen noch nicht veröffentlichten Beitrag ab (wartend oder angehalten).",
            _obj({"post_id": {"type": "string", "maxLength": 64}}, ["post_id"]),
            cancel,
            "low",
            C,
            writes=True,
            summarize=lambda a: f"Geplanten Beitrag {a['post_id'][:8]} abbrechen",
        ),
        ToolSpec(
            "uptik_history",
            "Veröffentlichungshistorie (veröffentlicht, fehlgeschlagen, abgebrochen, zu prüfen).",
            _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
            history,
            "read",
            C,
        ),
    ]


SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "runner": {
            "enum": ["local", "device"],
            "description": "Standard für neue Konten: Browser auf dem Core-Rechner "
            "(local) oder auf Sirs Rechner über den Device Agent (device)",
        },
        "device": {"type": "string", "maxLength": 120, "description": "Name des Device Agents (bei runner=device)"},
        "device_staging_dir": {"type": "string", "maxLength": 300, "description": "Ordner auf dem Gerät für Videos"},
        "headless": {
            "type": "boolean",
            "description": "Veröffentlichen ohne sichtbares Fenster (Anmeldung immer sichtbar)",
        },
        "browser_path": {"type": "string", "maxLength": 1024, "description": "Pfad zu Chrome (leer = automatisch)"},
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 6, "description": "Versuche je Beitrag"},
        "retry_base_s": {
            "type": "integer",
            "minimum": 30,
            "maximum": 3600,
            "description": "Erste Wartezeit (s), verdoppelt sich",
        },
        "max_parallel": {"type": "integer", "minimum": 1, "maximum": 3, "description": "Konten gleichzeitig"},
        "handoff_enabled": {"type": "boolean", "description": "Fertige Videos der Videoproduktion übernehmen"},
        "handoff_publish_time": {
            "type": "string",
            "pattern": "^(([01]\\d|2[0-3]):[0-5]\\d)?$",
            "description": "HH:MM (Berlin) für übernommene Videos; leer = sofort nach Freigabe",
        },
        "youtube_shorts_enabled": {
            "type": "boolean",
            "description": "YouTube Shorts als Ziel anbieten (experimentell)",
        },
        "login_timeout_s": {"type": "integer", "minimum": 60, "maximum": 1800},
        "video_dirs": {
            "type": "array",
            "items": {"type": "string", "maxLength": 1024},
            "maxItems": 20,
            "description": "Ordner, aus denen Videos veröffentlicht werden dürfen",
        },
    },
}


class UptikConnector(Connector):
    manifest = Manifest(
        id=CONNECTOR_ID,
        name="UpTik Autopost (TikTok, Reels, Shorts)",
        description="Veröffentlicht Videos über die Weboberflächen von TikTok, Instagram und optional YouTube – ohne "
        "Developer-App, mit Sirs eigener Anmeldung. Jede Veröffentlichung braucht eine Freigabe; bei Captcha, "
        "Prüfung oder Ratenlimit stoppt das Konto sofort.",
        category="business",
        auth="local",
        settings_schema=SETTINGS_SCHEMA,
        setup_instructions="Aktivieren, dann unter UpTik je Plattform ein Konto anlegen und „Anmelden“ wählen. "
        "Browser-Automatisierung verstößt gegen die Nutzungsbedingungen der Plattformen und kann Konten gefährden.",
        docs_url="docs/UPTIK_EVALUATION.md",
        mcp_note="Kein offizieller Weg ohne Developer-App; eigener Browser-Adapter nach dem Konzept von UpTik (MIT).",
        default_enabled=False,
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        from jarvis.uptik.handoff import SocialBridge

        self.svc: UptikService = state.uptik
        self.bridge = SocialBridge(self.svc)

    def tools(self) -> list[ToolSpec]:
        return uptik_tools(self.svc)

    async def start(self) -> None:
        await self.svc.start()
        self.bridge.start()

    async def stop(self) -> None:
        self.bridge.stop()
        await self.svc.stop()

    async def health(self) -> Health:
        st = await self.svc.status()
        tripped = [a for a in st["accounts"] if a["circuit_state"] != "ok"]
        if tripped:
            names = ", ".join(f"{a['label']} ({a['circuit_reason']})" for a in tripped)
            return Health(
                "degraded", f"Angehalten – manuelle Prüfung nötig: {names}"[:300], details={"tripped": len(tripped)}
            )
        connected = sum(1 for a in st["accounts"] if a["status"] == "connected")
        if not st["local_runner"]["ok"] and all(a["runner"] == "local" for a in st["accounts"]):
            return Health("degraded", st["local_runner"]["reason"])
        msg = f"{connected} Konto/Konten verbunden" if connected else "Noch kein Konto angemeldet"
        return Health("ok", msg, details={"connected": connected, "counts": st["counts"]})
