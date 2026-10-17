"""Agent tools for the phone: notify Sir, call him now, or call him once something is finished.

Risk ``low``: they only reach Sir himself (no third party). Calls are still bounded by the alert engine
(explicit instruction only, one call per event, rate limits, quiet hours, honest availability checks), and
a tainted session needs approval like any other non-read action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.mobile.alerts import CATEGORIES, Alert
from jarvis.tools.registry import ToolContext, ToolSpec

if TYPE_CHECKING:
    from jarvis.app_state import AppState


def mobile_tools(state: AppState) -> list[ToolSpec]:
    from jarvis.tools.executor import ToolUserError

    async def notify(ctx: ToolContext, a: dict[str, Any]) -> Any:
        res = await state.alerts.dispatch(
            Alert(
                a.get("category", "task"),
                a["title"],
                a.get("body", ""),
                "warning" if a.get("urgent") else "info",
                event_key=a.get("event_key") or "",
                deep_link=f"jarvis://chat/{ctx.session_id}" if ctx.session_id else "jarvis://home",
                data={"session_id": str(ctx.session_id)} if ctx.session_id else {},
                source="agent",
            )
        )
        if res.get("decision") in ("push", "silent") and not res.get("delivered"):
            raise ToolUserError("Keine Zustellung möglich: kein Smartphone mit Push-Registrierung oder Firebase fehlt.")
        return res

    async def call_now(ctx: ToolContext, a: dict[str, Any]) -> Any:
        blocker = await state.calls.call_blocker(None)
        if blocker:
            raise ToolUserError(f"Anruf nicht möglich: {blocker}")
        try:
            return await state.calls.ring_owner(message=a["message"], reason="agent")
        except RuntimeError as e:
            raise ToolUserError(f"Anruf nicht möglich: {e}") from e

    async def call_when(ctx: ToolContext, a: dict[str, Any]) -> Any:
        try:
            watch = await state.calls.add_watch(
                a["trigger"], a["message"], str(ctx.session_id) if ctx.session_id else None
            )
        except ValueError as e:
            raise ToolUserError(str(e)) from e
        av = await state.calls.availability()
        note = (
            ""
            if av["can_ring_owner"]
            else (
                " Hinweis: Ein Anruf ist derzeit nicht möglich ("
                + ", ".join(av["ring_problems"])
                + ") – dann kommt eine Benachrichtigung."
            )
        )
        return {"watch": watch, "note": note.strip()}

    trigger_schema = {
        "type": "object",
        "properties": {
            "type": {"enum": ["coding_job", "task", "time"]},
            "id": {"type": "string", "description": "Job-/Aufgaben-ID oder 'latest' für den zuletzt gestarteten"},
            "at": {"type": "string", "description": "ISO-Zeitpunkt (nur bei type=time)"},
        },
        "required": ["type"],
        "additionalProperties": False,
    }
    return [
        ToolSpec(
            "mobile_notify",
            "Schickt Sir eine Push-Benachrichtigung aufs Smartphone (Kategorie bestimmt Kanal/Ruhezeiten). "
            "Für Hinweise, die Sir unterwegs sehen soll.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 2, "maxLength": 120},
                    "body": {"type": "string", "maxLength": 800},
                    "category": {"enum": list(CATEGORIES)},
                    "urgent": {"type": "boolean"},
                    "event_key": {"type": "string", "description": "gleiches Ereignis → keine Doppelmeldung"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            notify,
            "low",
            "system",
            summarize=lambda a: f"Smartphone-Benachrichtigung: {a['title'][:80]}",
        ),
        ToolSpec(
            "mobile_call_owner",
            "Ruft Sir JETZT über die J.A.R.V.I.S.-App an (Internet-Anruf, kein Telefonnetz). NUR verwenden, wenn "
            "Sir ausdrücklich angerufen werden möchte. Für 'ruf mich an, sobald …' stattdessen mobile_call_when.",
            {
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 3, "maxLength": 500}},
                "required": ["message"],
                "additionalProperties": False,
            },
            call_now,
            "low",
            "system",
            summarize=lambda a: f"Sir per App anrufen: {a['message'][:100]}",
        ),
        ToolSpec(
            "mobile_call_when",
            "Merkt vor, Sir per App anzurufen, sobald ein Coding-Job/eine Aufgabe fertig ist oder zu einem Zeitpunkt "
            "– nur auf Sirs ausdrückliche Bitte („ruf mich an, wenn die Website fertig ist“). Erst den Auftrag "
            "starten, dann dieses Werkzeug mit dessen ID (oder 'latest'). message = was du beim Anruf sagst.",
            {
                "type": "object",
                "properties": {
                    "trigger": trigger_schema,
                    "message": {"type": "string", "minLength": 3, "maxLength": 500},
                },
                "required": ["trigger", "message"],
                "additionalProperties": False,
            },
            call_when,
            "low",
            "system",
            summarize=lambda a: f"Rückruf vormerken ({a['trigger'].get('type')}): {a['message'][:80]}",
        ),
    ]
