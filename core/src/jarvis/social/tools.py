"""Connector "Social Media" and its tools. Every action of the pipeline that leaves the Core goes through these
tools and therefore through the ToolExecutor (approval mode, risk, kill switch, idempotency ledger, audit):

* ``social_email_video`` – risk ``low`` like the ``mobile_*`` tools: it reaches only Sir. Sir approved the daily
  mail to his own address (2026-10-28); any other recipient is put to an approval (``ask_if``), in every mode.
* ``social_publish_video`` – risk ``high``: visible to third parties, so it needs an approval unless Sir allows it
  explicitly (approval mode YOLO or a per-tool rule "Sofort" in „Eigene Regeln“).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.social.platforms import PLATFORMS
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

if TYPE_CHECKING:
    from jarvis.social.service import SocialMediaService


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


def _uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ToolUserError("Ungültige Video-ID") from None


def owner_addresses(svc: SocialMediaService) -> set[str]:
    out = {svc.cfg.owner_email.strip().lower()}
    google = svc.state.connectors.connectors.get("google")
    acct = (getattr(google, "settings", {}) or {}).get("account_email") if google else None
    if acct:
        out.add(str(acct).strip().lower())
    return {a for a in out if a}


def social_tools(svc: SocialMediaService) -> list[ToolSpec]:
    S = "social_media"

    async def produce(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.produce(ctx.principal, hint=str(a.get("hint") or ""))

    async def list_videos(ctx: ToolContext, a: dict[str, Any]) -> Any:
        from jarvis.social.repo import video_dict

        rows = await svc.repo.list_videos(limit=int(a.get("limit") or 10))
        return {"videos": [video_dict(v, full=False) for v in rows], "schedule": await svc.schedule_info()}

    async def prepare_email(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
        to = str(a.get("to") or svc.settings.load().get("email_recipient") or svc.cfg.owner_email).strip()
        if "@" not in to or any(c in to for c in " ,;<>\n"):
            raise ToolUserError("Ungültige E-Mail-Adresse")
        return {**a, "to": to}

    def ask_email(a: dict[str, Any]) -> str | None:
        to = str(a.get("to") or "").strip().lower()
        if to and to not in owner_addresses(svc):
            return f"Empfänger {to} ist nicht Sir – Versand nur mit Freigabe"
        return None

    async def email(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.send_email(_uuid(a["video_id"]), a["to"])

    async def publish(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.publish(_uuid(a["video_id"]), a["platform"])

    return [
        ToolSpec(
            "social_produce_video",
            "Startet sofort die Produktion eines neuen PJE-Systems-Social-Videos (Idee, Storyboard, Render, QA, "
            "Captions, Bibliothek, E-Mail an Sir). Optional hint: Wunsch für Thema/Format.",
            _obj({"hint": {"type": "string", "maxLength": 500}}),
            produce,
            "low",
            S,
            writes=True,
            summarize=lambda a: "Social-Video produzieren" + (f": {a['hint'][:120]}" if a.get("hint") else ""),
        ),
        ToolSpec(
            "social_list_videos",
            "Listet die zuletzt produzierten Social-Videos (Titel, Status, Kategorie) und den Zeitplan.",
            _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
            list_videos,
            "read",
            S,
        ),
        ToolSpec(
            "social_email_video",
            "Schickt ein fertiges Social-Video mit Captions per E-Mail (Standard: an Sir). "
            "Andere Empfänger nur mit Freigabe.",
            _obj({"video_id": {"type": "string"}, "to": {"type": "string", "maxLength": 254}}, ["video_id"]),
            email,
            "low",
            S,
            writes=True,
            timeout_s=120,
            prepare=prepare_email,
            ask_if=ask_email,
            summarize=lambda a: f"Social-Video per E-Mail an {a.get('to', 'Sir')}",
        ),
        ToolSpec(
            "social_publish_video",
            "Veröffentlicht ein fertiges Social-Video auf einer verbundenen Plattform "
            "(instagram, facebook, tiktok, youtube).",
            _obj({"video_id": {"type": "string"}, "platform": {"enum": list(PLATFORMS)}}, ["video_id", "platform"]),
            publish,
            "high",
            S,
            writes=True,
            timeout_s=900,
            summarize=lambda a: f"Social-Video auf {a['platform']} veröffentlichen (öffentlich sichtbar)",
        ),
    ]


class SocialMediaConnector(Connector):
    manifest = Manifest(
        id="social_media",
        name="Social Media (PJE Systems)",
        description="Tägliches Werbevideo für PJE Systems: Idee, Remotion-Render, Qualitätskontrolle, Captions, "
        "Videobibliothek, E-Mail an Sir; Veröffentlichen nur mit verbundenen Plattformen und Freigabe.",
        category="business",
        auth="local",
        docs_url="docs/SOCIAL_MEDIA.md",
        mcp_note="Lokale Pipeline (Remotion + FFmpeg); Plattformen über ihre offiziellen APIs.",
        default_enabled=True,
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self.svc: SocialMediaService = state.social

    def tools(self) -> list[ToolSpec]:
        return social_tools(self.svc)

    async def health(self) -> Health:
        deps = self.svc.deps
        if deps.get("ok") is None:
            deps = await self.svc.refresh_dependencies()
        if not deps.get("ok"):
            return Health("error", "; ".join(deps.get("problems") or [])[:300], details=deps)
        return Health("ok", "Remotion, Headless-Browser und FFmpeg bereit", details={"browser": deps.get("browser")})
