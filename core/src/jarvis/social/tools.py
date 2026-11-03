"""Connector "Social Media" and its tools. Every action of the pipeline that leaves the Core goes through these
tools and therefore through the ToolExecutor (approval mode, risk, kill switch, idempotency ledger, audit):

* ``social_email_video`` – risk ``low`` like the ``mobile_*`` tools: without ``to`` it goes to the recipient list
  of the video's company profile. Sir approved the daily mail to his own address (2026-10-28) and to the configured
  ``social_media.video_recipients`` (Bagy); any other recipient – also one Sir put into a profile in the app – is
  put to an approval (``ask_if``), in every mode.

Every tool takes an optional ``company`` (profile id or name; default: the active profile, PJE Systems unless Sir
switched). For e-mail and publishing it guards that the video belongs to that company.
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


def _split(raw: Any) -> list[str]:
    return [p.strip() for p in str(raw or "").split(",") if p.strip()]


def approved_addresses(svc: SocialMediaService) -> set[str]:
    """Sir's addresses plus the configured video recipients: mails to them need no extra approval."""
    return owner_addresses(svc) | {r.strip().lower() for r in svc.cfg.video_recipients if r.strip()}


COMPANY = {
    "type": "string",
    "maxLength": 80,
    "description": "Unternehmensprofil (Name oder ID, z. B. „PJE Systems“ oder „Apo HR“); Standard: aktives Profil",
}


def social_tools(svc: SocialMediaService) -> list[ToolSpec]:
    S = "social_media"

    async def video_of(a: dict[str, Any]) -> Any:
        """The video, checked against an explicitly named company."""
        v = await svc.repo.get(_uuid(a["video_id"]))
        if v is None:
            raise ToolUserError("Video nicht gefunden")
        if a.get("company"):
            profile = svc.company(str(a["company"]))
            if v.company_id != profile["id"]:
                raise ToolUserError(f"Das Video gehört zu {svc.profile_of(v)['name']}, nicht zu {profile['name']}")
        return v

    async def produce(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.produce(ctx.principal, hint=str(a.get("hint") or ""), company=a.get("company"))

    async def list_videos(ctx: ToolContext, a: dict[str, Any]) -> Any:
        from jarvis.social.repo import video_dict

        profile = svc.company(a.get("company"))
        rows = await svc.repo.list_videos(limit=int(a.get("limit") or 10), company_id=profile["id"])
        return {
            "company": profile["name"],
            "videos": [video_dict(v, full=False) for v in rows],
            "schedule": await svc.schedule_info(profile["id"]),
        }

    async def prepare_email(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
        v = await video_of(a)
        explicit = str(a.get("to") or "").strip()
        recipients = [explicit] if explicit else svc.video_recipients(svc.profile_of(v))
        for to in recipients:
            if "@" not in to or any(c in to for c in " ,;<>\n"):
                raise ToolUserError("Ungültige E-Mail-Adresse")
        return {**a, "to": ", ".join(recipients)}  # prepared "to" is the comma-separated final recipient list

    def ask_email(a: dict[str, Any]) -> str | None:
        known = approved_addresses(svc)
        others = [r.lower() for r in _split(a.get("to")) if r.lower() not in known]
        if others:
            return f"Empfänger {', '.join(others)} nicht freigegeben – Versand nur mit Freigabe"
        return None

    async def email(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await svc.send_emails(_uuid(a["video_id"]), _split(a["to"]))

    async def publish(ctx: ToolContext, a: dict[str, Any]) -> Any:
        await video_of(a)
        return await svc.publish(_uuid(a["video_id"]), a["platform"])

    return [
        ToolSpec(
            "social_produce_video",
            "Startet sofort die Produktion eines neuen Social-Videos für ein Unternehmen (Idee, Storyboard, Render, "
            "QA, Captions, Bibliothek, E-Mail an die Empfängerliste). Optional company (Standard: aktives Profil, "
            "sonst PJE Systems) und hint: Sirs Vorgabe zu Thema, Angebot oder Preis – sie hat Vorrang vor den "
            "Standardwerten des Profils (z. B. „Angebot für 299 €“).",
            _obj({"hint": {"type": "string", "maxLength": 500}, "company": COMPANY}),
            produce,
            "low",
            S,
            writes=True,
            summarize=lambda a: "Social-Video produzieren"
            + (f" ({a['company']})" if a.get("company") else "")
            + (f": {a['hint'][:120]}" if a.get("hint") else ""),
        ),
        ToolSpec(
            "social_list_videos",
            "Listet die zuletzt produzierten Social-Videos eines Unternehmens (Titel, Status, Kategorie) und seinen "
            "Zeitplan. Optional company (Standard: aktives Profil).",
            _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}, "company": COMPANY}),
            list_videos,
            "read",
            S,
        ),
        ToolSpec(
            "social_email_video",
            "Schickt ein fertiges Social-Video mit Captions per E-Mail (ohne „to“: an die Empfängerliste des "
            "Unternehmens, bei PJE Systems Sir und Bagy). Nicht freigegebene Empfänger nur mit Freigabe. Optional "
            "company: prüft, dass das Video zu diesem Unternehmen gehört.",
            _obj(
                {"video_id": {"type": "string"}, "to": {"type": "string", "maxLength": 254}, "company": COMPANY},
                ["video_id"],
            ),
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
            "(instagram, facebook, tiktok, youtube) – nur über die Konten seines Unternehmens. Optional company: "
            "prüft, dass das Video zu diesem Unternehmen gehört.",
            _obj(
                {"video_id": {"type": "string"}, "platform": {"enum": list(PLATFORMS)}, "company": COMPANY},
                ["video_id", "platform"],
            ),
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
        name="Social Media",
        description="Tägliches Werbevideo je Unternehmensprofil (PJE Systems, Apo HR …): Idee, Remotion-Render, "
        "Qualitätskontrolle, Captions, Videobibliothek, E-Mail an die Empfängerliste; Veröffentlichen nur über die "
        "Konten des Unternehmens und mit Freigabe.",
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
