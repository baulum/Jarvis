"""Coding connector: projects, git clone and Claude Code jobs."""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.coding.runner import CodingError, CodingRunner, slug
from jarvis.connectors.base import Connector, Health, Manifest
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
        default_enabled=True,
        setup_instructions="Claude Code installieren und einmalig `claude` → /login mit der Claude-Subscription.",
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
            try:
                job = await runner().create_job(
                    a["title"],
                    a["instructions"],
                    a["project_path"],
                    test_command=a.get("test_command"),
                    actor=ctx.principal.label,
                )
            except CodingError as e:
                raise ToolUserError(str(e)) from e
            return {
                "job_id": str(job.id),
                "status": "queued",
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
                    "result": job.result,
                }

        async def list_jobs(ctx: ToolContext, a: dict[str, Any]) -> Any:
            from sqlalchemy import select

            from jarvis.db import session_scope
            from jarvis.db.models import CodingJob

            async with session_scope() as s:
                rows = (await s.execute(select(CodingJob).order_by(CodingJob.created_at.desc()).limit(10))).scalars()
                return [{"job_id": str(j.id), "title": j.title, "status": j.status} for j in rows]

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
                "Worktree). Auch zum Reparieren eigener Werkzeuge und Anbindungen (z. B. Lead-Tool-MCP, Export, Mailversand) nutzbar. project_path = Pfad des Projekts. Optional test_command, z. B. 'npm run build'.",
                _obj(
                    {
                        "title": {"type": "string"},
                        "instructions": {"type": "string", "minLength": 10},
                        "project_path": {"type": "string"},
                        "test_command": {"type": "string"},
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
                "Bricht einen Coding-Job ab.",
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
                "gehen dabei verloren).",
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
                "coding_prepare_pr",
                "Pusht den Job-Branch und erstellt einen Draft-Pull-Request auf GitHub.",
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
