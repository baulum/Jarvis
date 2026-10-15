"""System connector: tasks/playbooks, scheduler, notifications, status and time."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from croniter import croniter

from jarvis.agent.persona import TZ
from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec


def _obj(props: dict[str, Any], req: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


class SystemConnector(Connector):
    manifest = Manifest(
        id="system",
        name="Jarvis System",
        description="Aufgaben (Playbooks), Zeitplanung, Benachrichtigungen und Systemstatus.",
        category="system",
        auth="none",
        default_enabled=True,
    )

    async def health(self) -> Health:
        return Health("ok", "Kernfunktionen aktiv")

    def tools(self) -> list[ToolSpec]:
        st = self.state

        async def now(ctx: ToolContext, a: dict[str, Any]) -> Any:
            n = datetime.now(TZ)
            return {
                "iso": n.isoformat(timespec="seconds"),
                "weekday": n.strftime("%A"),
                "human": n.strftime("%d.%m.%Y %H:%M"),
            }

        async def status(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await st.system_status(brief=True)

        async def playbooks(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return [
                {"name": p.name, "title": p.title, "description": p.description, "params": p.params_schema}
                for p in st.tasks.playbooks.values()
            ]

        async def start_playbook(ctx: ToolContext, a: dict[str, Any]) -> Any:
            if a["playbook"] not in st.tasks.playbooks:
                raise ToolUserError(f"Unbekanntes Playbook. Verfügbar: {', '.join(st.tasks.playbooks)}")
            task = await st.tasks.create(
                a["playbook"],
                a.get("params") or {},
                ctx.principal,
                session_id=ctx.session_id,
                idempotency_key=ctx.idempotency_key,
            )
            return {
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status,
                "steps": [s.name for s in task.steps],
                "note": "Die Aufgabe läuft im Hintergrund; Fortschritt in der App oder per task_status.",
                "nicht_eingerichtet": [
                    conn.manifest.name
                    for cid, conn in st.connectors.connectors.items()
                    if cid in ("lexware", "google", "telegram", "github") and not conn.active
                ],
                "hinweis": "Schritte mit nicht eingerichteten Connectoren werden übersprungen – sag Sir das ehrlich.",
            }

        async def task_status(ctx: ToolContext, a: dict[str, Any]) -> Any:
            task = await st.tasks.get_task(_uuid(a["task_id"]))
            if task is None:
                raise ToolUserError("Aufgabe nicht gefunden")
            return {
                "id": str(task.id),
                "title": task.title,
                "status": task.status,
                "error": task.error,
                "question": task.pending_question,
                "steps": [{"name": s.name, "status": s.status, "error": s.error} for s in task.steps],
            }

        async def task_list(ctx: ToolContext, a: dict[str, Any]) -> Any:
            tasks = await st.tasks.list_tasks(a.get("status"), limit=15)
            return [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "status": t.status,
                    "created": t.created_at.isoformat(timespec="minutes"),
                }
                for t in tasks
            ]

        async def task_input(ctx: ToolContext, a: dict[str, Any]) -> Any:
            task = await st.tasks.provide_input(_uuid(a["task_id"]), a["values"], ctx.principal.label)
            return {"status": task.status if task else "unknown"}

        async def task_cancel(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return {"cancelled": await st.tasks.cancel(_uuid(a["task_id"]), ctx.principal.label)}

        async def schedule_create(ctx: ToolContext, a: dict[str, Any]) -> Any:
            if not croniter.is_valid(a["cron"]):
                raise ToolUserError("Ungültiger Cron-Ausdruck (Format: Minute Stunde Tag Monat Wochentag)")
            sched = await st.scheduler.create(
                a["name"], a["cron"], {"type": "prompt", "prompt": a["prompt"]}, ctx.principal.label
            )
            return {"id": str(sched.id), "next_run": sched.next_run_at.isoformat() if sched.next_run_at else None}

        async def schedule_list(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "cron": s.cron,
                    "enabled": s.enabled,
                    "next_run": s.next_run_at.isoformat() if s.next_run_at else None,
                }
                for s in await st.scheduler.list()
            ]

        async def notify(ctx: ToolContext, a: dict[str, Any]) -> Any:
            delivered = await st.notifier.notify(
                "agent.notify", a["title"], a.get("body", ""), channels=a.get("channels"), level=a.get("level", "info")
            )
            return {"delivered": delivered}

        return [
            ToolSpec(
                "system_time", "Aktuelles Datum und Uhrzeit (Europe/Berlin).", _obj({}, []), now, "read", "system"
            ),
            ToolSpec(
                "system_status",
                "Status aller Jarvis-Komponenten, Connectors und offenen Freigaben.",
                _obj({}, []),
                status,
                "read",
                "system",
            ),
            ToolSpec(
                "task_list_playbooks",
                "Listet verfügbare mehrstufige Abläufe (Playbooks) mit Parametern.",
                _obj({}, []),
                playbooks,
                "read",
                "system",
            ),
            ToolSpec(
                "task_start_playbook",
                "Startet einen mehrstufigen, nachvollziehbaren Ablauf im Hintergrund. Beispiel: "
                "playbook='new_customer_project', params={customer_name, project_type, price_eur, ...} "
                "für 'neuer Kunde + Rechnung + Website-Projekt'.",
                _obj({"playbook": {"type": "string"}, "params": {"type": "object"}}, ["playbook", "params"]),
                start_playbook,
                "low",
                "system",
                summarize=lambda a: f"Ablauf '{a['playbook']}' starten",
            ),
            ToolSpec(
                "task_status",
                "Status einer Aufgabe inkl. aller Schritte.",
                _obj({"task_id": {"type": "string"}}, ["task_id"]),
                task_status,
                "read",
                "system",
            ),
            ToolSpec(
                "task_list",
                "Listet aktuelle Aufgaben.",
                _obj({"status": {"type": "string"}}, []),
                task_list,
                "read",
                "system",
            ),
            ToolSpec(
                "task_provide_input",
                "Liefert fehlende Angaben für eine wartende Aufgabe nach.",
                _obj({"task_id": {"type": "string"}, "values": {"type": "object"}}, ["task_id", "values"]),
                task_input,
                "low",
                "system",
            ),
            ToolSpec(
                "task_cancel",
                "Bricht eine Aufgabe ab.",
                _obj({"task_id": {"type": "string"}}, ["task_id"]),
                task_cancel,
                "low",
                "system",
            ),
            ToolSpec(
                "schedule_create",
                "Plant eine wiederkehrende Aufgabe (Cron, Europe/Berlin), die Jarvis "
                "zum Zeitpunkt als Anweisung ausführt.",
                _obj(
                    {"name": {"type": "string"}, "cron": {"type": "string"}, "prompt": {"type": "string"}},
                    ["name", "cron", "prompt"],
                ),
                schedule_create,
                "low",
                "system",
                summarize=lambda a: f"Zeitplan '{a['name']}' ({a['cron']}) anlegen",
            ),
            ToolSpec("schedule_list", "Listet geplante Aufgaben.", _obj({}, []), schedule_list, "read", "system"),
            ToolSpec(
                "notify_owner",
                "Benachrichtigt Sir (Desktop, Telegram oder Anruf je nach Regel).",
                _obj(
                    {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "level": {"type": "string", "enum": ["info", "warning", "critical"]},
                        "channels": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["desktop", "telegram", "voice"]},
                        },
                    },
                    ["title"],
                ),
                notify,
                "low",
                "system",
            ),
        ]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as e:
        raise ToolUserError("Ungültige ID") from e
