"""Coding connector: projects, git clone and Claude Code jobs."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from jarvis.agent.attachments import (
    MAX_PER_MESSAGE,
    NAME_PATTERN,
    AttachmentError,
    job_attachment_note,
    stage_for_job,
)
from jarvis.agent.model_router import EFFORTS
from jarvis.coding.runner import SUPABASE_PASSWORD_SECRET, CodingError, CodingRunner, slug
from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class CodingConnector(Connector):
    manifest = Manifest(
        id="coding",
        name="Claude Code",
        description="Projekte anlegen, Repositories klonen und Entwicklungsaufträge an die offizielle Claude Code "
        "CLI (Subscription-Login) übergeben – isoliert in Git-Worktrees mit Sandbox.",
        category="developer",
        auth="local",
        secrets=[
            SecretField(
                SUPABASE_PASSWORD_SECRET,
                "Supabase-Standard-Datenbankpasswort",
                "Wird beim Supabase-Deploy (db push) zuerst verwendet. Lehnt ein Projekt es ab, fragt Jarvis nach "
                "dem Passwort dieses Projekts und merkt es sich pro Projekt.",
                required=False,
            )
        ],
        default_enabled=True,
        setup_instructions="Claude Code installieren und einmalig den unter coding.claude_command konfigurierten "
        "Befehl (Standard `claudeb`) starten → /login mit der Claude-Subscription.",
    )

    async def health(self) -> Health:
        st = await self.state.coding.auth_status()
        if st["ok"]:
            return Health("ok", f"Claude Code angemeldet ({st['authMethod']})")
        return Health("error", st.get("error") or "nicht angemeldet")

    def tools(self) -> list[ToolSpec]:
        def runner() -> CodingRunner:
            return self.state.coding

        S = "coding"

        async def project_create(ctx: ToolContext, a: dict[str, Any]) -> Any:
            path = self.state.settings.paths.projects_dir / slug(a["name"], 50)
            existed = path.exists()
            await runner().ensure_repo(path)
            if a.get("brief"):
                (path / "BRIEF.md").write_text(a["brief"], encoding="utf-8")
            return {"path": str(path), "created": not existed}

        async def clone(ctx: ToolContext, a: dict[str, Any]) -> Any:
            try:
                return {"path": str(await runner().clone(a["url"], a.get("name")))}
            except CodingError as e:
                raise ToolUserError(str(e)) from e

        async def start_job(ctx: ToolContext, a: dict[str, Any]) -> Any:
            instructions = a["instructions"]
            if a.get("attachments"):
                try:
                    _, staged = await asyncio.to_thread(stage_for_job, self.state, list(a["attachments"]))
                except AttachmentError as e:
                    raise ToolUserError(str(e)) from e
                instructions += job_attachment_note(staged)
            try:
                job = await runner().create_job(
                    a["title"],
                    instructions,
                    a["project_path"],
                    test_command=a.get("test_command"),
                    actor=ctx.principal.label,
                    model=a.get("model"),
                    effort=a.get("effort"),
                )
            except CodingError as e:
                raise ToolUserError(str(e)) from e
            return {
                "job_id": str(job.id),
                "status": "queued",
                "model": job.model,
                "effort": job.effort,
                "note": "Claude Code arbeitet im Hintergrund; Fortschritt in der App, Meldung bei Abschluss.",
            }

        async def status(ctx: ToolContext, a: dict[str, Any]) -> Any:
            from jarvis.db import session_scope
            from jarvis.db.models import CodingJob

            async with session_scope() as s:
                job = await s.get(CodingJob, _uuid(a["job_id"]))
                if job is None:
                    raise ToolUserError("Job nicht gefunden")
                return {
                    "job_id": str(job.id),
                    "title": job.title,
                    "status": job.status,
                    "branch": job.branch,
                    "worktree": job.worktree_path,
                    "model": job.model,
                    "effort": job.effort,
                    "result": job.result,
                }

        async def list_jobs(ctx: ToolContext, a: dict[str, Any]) -> Any:
            from sqlalchemy import select

            from jarvis.db import session_scope
            from jarvis.db.models import CodingJob

            async with session_scope() as s:
                rows = (await s.execute(select(CodingJob).order_by(CodingJob.created_at.desc()).limit(10))).scalars()
                return [
                    {"job_id": str(j.id), "title": j.title, "status": j.status, "model": j.model, "effort": j.effort}
                    for j in rows
                ]

        async def cancel(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return {"cancelled": await runner().cancel(_uuid(a["job_id"]), ctx.principal.label)}

        async def delete(ctx: ToolContext, a: dict[str, Any]) -> Any:
            try:
                return await runner().delete(
                    _uuid(a["job_id"]), ctx.principal.label, delete_worktree=bool(a.get("delete_worktree", False))
                )
            except CodingError as e:
                raise ToolUserError(str(e)) from e

        async def prepare_pr(ctx: ToolContext, a: dict[str, Any]) -> Any:
            try:
                return await runner().prepare_pr(_uuid(a["job_id"]), a["repo"], a.get("base", "main"))
            except CodingError as e:
                raise ToolUserError(str(e)) from e

        async def ship_job(ctx: ToolContext, a: dict[str, Any]) -> Any:
            try:
                return await runner().ship(_uuid(a["job_id"]), ctx.principal.label)
            except CodingError as e:
                raise ToolUserError(str(e)) from e

        async def store_supabase_password(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
            # Runs before the approval: the password goes straight into the Keychain, so what is approved,
            # executed, audited and kept in the idempotency ledger afterwards only names job and project.
            if "password" not in a:
                return a
            try:
                ref = await runner().store_supabase_password(_uuid(a["job_id"]), a["password"])
            except CodingError as e:
                raise ToolUserError(str(e)) from e
            return {"job_id": a["job_id"], "project_ref": ref}

        async def supabase_password(ctx: ToolContext, a: dict[str, Any]) -> Any:
            if "password" in a:
                a = await store_supabase_password(ctx, a)
            elif not a.get("project_ref"):
                raise ToolUserError("Das Datenbankpasswort fehlt (password)")
            try:
                ship = await runner().ship(_uuid(a["job_id"]), ctx.principal.label)
            except CodingError as e:
                raise ToolUserError(str(e)) from e
            return {"project_ref": a["project_ref"], "password_stored": True, "ship": ship}

        return [
            ToolSpec(
                "project_create",
                "Legt ein neues Projektverzeichnis mit Git-Repository an (optional mit BRIEF.md).",
                _obj({"name": {"type": "string", "minLength": 2}, "brief": {"type": "string"}}, ["name"]),
                project_create,
                "low",
                S,
                writes=True,
            ),
            ToolSpec(
                "git_clone",
                "Klont ein Git-Repository (https) in den Jarvis-Workspace. Code wird nicht ausgeführt.",
                _obj({"url": {"type": "string"}, "name": {"type": "string"}}, ["url"]),
                clone,
                "medium",
                S,
                writes=True,
                timeout_s=300,
                summarize=lambda a: f"Repository klonen: {a['url']}",
            ),
            ToolSpec(
                "coding_start_job",
                "Übergibt einen Entwicklungsauftrag an Claude Code (eigener Git-Worktree, "
                "Worktree). Auch zum Reparieren eigener Werkzeuge und Anbindungen (z. B. Lead-Tool-MCP, Export, "
                "Mailversand) nutzbar. project_path = Pfad des Projekts. Optional test_command, z. B. "
                "'npm run build'. Standard-Ablauf nach dem Job (automatisch, nicht selbst pushen/mergen): nur bei "
                "grünem Ergebnis wird der Job-Branch gepusht, per PR nach main gemergt, Supabase (Functions/"
                "Migrationen, falls vorhanden) deployt und danach Branch und Worktree gelöscht; bei rotem Ergebnis, "
                "Fehler oder Abbruch wird die Arbeit committet und nur der Job-Branch gepusht, nie main (Ergebnis "
                "unter result.ship, Probleme unter result.delivery_error). Betrifft der Job Jarvis selbst, startet "
                "Jarvis danach Core und offene Desktop-App neu (result.ship.self_restart, Fehler unter "
                "result.warnings). "
                "Optional model (haiku|sonnet|opus|auto) und effort (low|medium|high|xhigh|max): nennt Sir im Auftrag "
                "ein Modell (z. B. 'Opus 5.5') oder einen Effort, hier übergeben; ohne Angabe gilt das Standardmodell "
                "bzw. das im Chat gewählte. Modell- und Effort-Angaben von Sir zu einem Coding-Job gelten für den "
                "Job, nie für das Chat-Modell (`agent_set_model`). Will Sir einen laufenden oder gerade gestarteten "
                "Job mit anderem Modell/Effort, erst `coding_cancel_job`, dann hier neu starten (model/effort wie "
                "genannt, nicht genannte Werte vom bisherigen Job übernehmen) und in der Antwort ausdrücklich "
                "nennen, mit welchem Modell und Effort gestartet wurde. 'Sonnet'/'Opus'/'Haiku' ohne Zahl = "
                "aktuelle Version 5.5; '5.5' ohne Familie = Familie des bisherigen Jobs. "
                "Mit attachments gibt Jarvis Dateien aus dem Chat (Fotos, Screenshots, PDFs, Dokumente) an "
                "den Job weiter; sie werden kopiert und in den Anweisungen mit Pfad genannt.",
                _obj(
                    {
                        "title": {"type": "string"},
                        "instructions": {"type": "string", "minLength": 10},
                        "project_path": {"type": "string"},
                        "test_command": {"type": "string"},
                        "model": {"type": "string", "enum": ["haiku", "sonnet", "opus", "auto"]},
                        "effort": {"type": "string", "enum": list(EFFORTS)},
                        "attachments": {
                            "type": "array",
                            "items": {"type": "string", "pattern": NAME_PATTERN},
                            "maxItems": MAX_PER_MESSAGE,
                            "description": "Chat-Anhänge (file aus <anhaenge>), die Claude Code lesen soll – "
                            "Fotos, Screenshots, PDFs, Dokumente",
                        },
                    },
                    ["title", "instructions", "project_path"],
                ),
                start_job,
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Claude-Code-Auftrag '{a['title']}' in {a['project_path']}",
            ),
            ToolSpec(
                "coding_job_status",
                "Status und Ergebnis eines Coding-Jobs.",
                _obj({"job_id": {"type": "string"}}, ["job_id"]),
                status,
                "read",
                S,
            ),
            ToolSpec("coding_list_jobs", "Letzte Coding-Jobs.", _obj({}), list_jobs, "read", S),
            ToolSpec(
                "coding_cancel_job",
                "Bricht einen Coding-Job ab. Auch der erste Schritt, wenn Sir einen laufenden Job mit anderem "
                "Modell oder Effort neu starten will (danach `coding_start_job` mit model/effort); bei "
                "ausdrücklichem Wunsch ohne Rückfrage ausführen.",
                _obj({"job_id": {"type": "string"}}, ["job_id"]),
                cancel,
                "low",
                S,
            ),
            ToolSpec(
                "coding_delete_job",
                "Entfernt einen fehlgeschlagenen oder abgebrochenen Coding-Job (Status failed/cancelled) aus der "
                "Jobliste. Laufende und abgeschlossene Jobs werden abgelehnt. Git-Branch und Arbeitsordner bleiben "
                "erhalten; nur mit delete_worktree=true wird der Worktree entfernt (nicht committete Änderungen "
                "werden vorher als WIP-Commit auf dem Branch gesichert).",
                _obj(
                    {"job_id": {"type": "string"}, "delete_worktree": {"type": "boolean", "default": False}},
                    ["job_id"],
                ),
                delete,
                "low",
                S,
                writes=True,
                summarize=lambda a: (
                    f"Coding-Job {a['job_id'][:8]} aus der Jobliste entfernen"
                    + (" (inkl. Worktree)" if a.get("delete_worktree") else "")
                ),
            ),
            ToolSpec(
                "coding_ship_job",
                "Liefert einen grünen Coding-Job erneut aus, wenn die automatische Auslieferung nicht durchlief "
                "(Ergebnis unter result.ship): Job-Branch pushen, Pull Request erstellen und mergen, danach "
                "Supabase deployen (falls im Projekt). Setzt nach bereits erfolgtem PR/Merge dort fort. Wartet die "
                "Auslieferung auf ein Supabase-Datenbankpasswort (result.ship.state = 'waiting_input'), Sir danach "
                "fragen und coding_supabase_password nutzen.",
                _obj({"job_id": {"type": "string"}}, ["job_id"]),
                ship_job,
                "high",
                S,
                writes=True,
                timeout_s=1800,
                summarize=lambda a: f"Job {a['job_id'][:8]} ausliefern: PR mergen und Supabase deployen",
            ),
            ToolSpec(
                "coding_supabase_password",
                "Nur wenn eine Auslieferung auf das Supabase-Datenbankpasswort wartet (result.ship.state = "
                "'waiting_input', result.ship.supabase.password_request): password = das Datenbankpasswort, das Sir "
                "für dieses Supabase-Projekt nennt. Es wird pro Projekt im Schlüsselbund gespeichert (künftig vor "
                "dem Standardpasswort verwendet) und der Deploy fortgesetzt. Das Passwort nie wiederholen, "
                "zusammenfassen oder anderswo speichern.",
                _obj(
                    {
                        "job_id": {"type": "string"},
                        "password": {"type": "string", "minLength": 1},
                        "project_ref": {"type": "string"},
                    },
                    ["job_id"],
                ),
                supabase_password,
                "high",
                S,
                writes=True,
                timeout_s=1800,
                prepare=store_supabase_password,
                private_args=("password",),
                summarize=lambda a: (
                    f"Supabase-Passwort für Projekt {a.get('project_ref') or '?'} gespeichert – "
                    f"Job {a['job_id'][:8]} weiter ausliefern (Supabase deployen)"
                ),
            ),
            ToolSpec(
                "coding_prepare_pr",
                "Nur auf ausdrücklichen Wunsch (kein Standard – grüne Jobs werden automatisch gepusht, per PR "
                "gemergt und deployt): pusht den Job-Branch und erstellt einen Draft-Pull-Request auf GitHub, "
                "ohne zu mergen oder zu deployen.",
                _obj(
                    {"job_id": {"type": "string"}, "repo": {"type": "string"}, "base": {"type": "string"}},
                    ["job_id", "repo"],
                ),
                prepare_pr,
                "high",
                S,
                writes=True,
                timeout_s=180,
                summarize=lambda a: f"Branch pushen und PR in {a['repo']} erstellen (Job {a['job_id'][:8]})",
            ),
        ]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as e:
        raise ToolUserError("Ungültige ID") from e
