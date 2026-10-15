"""Obsidian / memory connector: long-term knowledge tools for the agent."""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.memory.vault import KIND_FOLDERS
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

KINDS = list(KIND_FOLDERS)


class ObsidianMemoryConnector(Connector):
    manifest = Manifest(
        id="obsidian",
        name="Obsidian Gedächtnis",
        description="Langzeitgedächtnis als Obsidian-Vault mit semantischer Suche (pgvector) und Wissensgraph.",
        category="knowledge",
        auth="local",
        default_enabled=True,
        setup_instructions="Keine Zugangsdaten nötig. Den Vault-Ordner (paths.vault_dir) in Obsidian als "
        "Vault öffnen, um das Gedächtnis zu lesen und zu bearbeiten.",
        mcp_note="Kein offizieller Obsidian-MCP-Server; Jarvis liest/schreibt die Markdown-Dateien direkt, "
        "damit der Vault ohne Plugin unabhängig lesbar bleibt.",
    )

    async def health(self) -> Health:
        stats = await self.state.memory.stats()
        return Health("ok", f"{stats.get('active', 0)} Erinnerungen", details=stats)

    def tools(self) -> list[ToolSpec]:
        mem = self.state.memory

        async def search(ctx: ToolContext, a: dict[str, Any]) -> Any:
            hits = await mem.search(
                a["query"],
                limit=min(int(a.get("limit", 5)), 10),
                kinds=[a["kind"]] if a.get("kind") else None,
                include_sensitive=ctx.principal.role == "owner",
            )
            return {
                "results": [
                    {k: h[k] for k in ("id", "title", "kind", "content", "path", "score", "updated_at")} for h in hits
                ],
                "hint": "Quelle = path im Obsidian-Vault",
            }

        async def save(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await mem.remember(
                a["title"],
                a["content"],
                a.get("kind", "note"),
                entities=a.get("entities") or [],
                sensitive=a.get("sensitive"),
                source={"type": "agent", "session_id": str(ctx.session_id or "")},
                actor=ctx.principal.label,
            )

        async def get(ctx: ToolContext, a: dict[str, Any]) -> Any:
            item = await mem.get(_uuid(a["id"]))
            if item is None:
                raise ToolUserError("Erinnerung nicht gefunden")
            if item["sensitivity"] == "sensitive" and ctx.principal.role != "owner":
                raise ToolUserError("Sensibler Eintrag – nur am Desktop einsehbar")
            return item

        async def correct(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await mem.correct(_uuid(a["id"]), a["new_content"], ctx.principal.label, a.get("new_title"))

        async def forget(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await mem.forget(_uuid(a["id"]), ctx.principal.label)

        def p(props: dict[str, Any], req: list[str]) -> dict[str, Any]:
            return {"type": "object", "properties": props, "required": req, "additionalProperties": False}

        return [
            ToolSpec(
                "memory_search",
                "Durchsucht Sirs Langzeitgedächtnis (Kunden, Projekte, Präferenzen, "
                "Entscheidungen, Abläufe) semantisch. Liefert Treffer mit Quellpfad.",
                p(
                    {
                        "query": {"type": "string", "minLength": 2},
                        "kind": {"type": "string", "enum": KINDS},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    ["query"],
                ),
                search,
                "read",
                "obsidian",
            ),
            ToolSpec(
                "memory_save",
                "Speichert dauerhaft relevantes Wissen im Gedächtnis (Duplikate werden "
                "erkannt und zusammengeführt). Sensible Daten warten auf Sirs Bestätigung.",
                p(
                    {
                        "title": {"type": "string", "minLength": 2, "maxLength": 200},
                        "content": {"type": "string", "minLength": 3},
                        "kind": {"type": "string", "enum": KINDS},
                        "entities": {"type": "array", "items": {"type": "string"}},
                        "sensitive": {"type": "boolean"},
                    },
                    ["title", "content"],
                ),
                save,
                "low",
                "obsidian",
                writes=True,
                summarize=lambda a: f"Gedächtnis: '{a['title']}' speichern",
            ),
            ToolSpec(
                "memory_get",
                "Liest eine Erinnerung vollständig inkl. Rückverweisen.",
                p({"id": {"type": "string"}}, ["id"]),
                get,
                "read",
                "obsidian",
            ),
            ToolSpec(
                "memory_correct",
                "Ersetzt veraltete Information. Die alte Notiz bleibt als 'veraltet' nachvollziehbar erhalten.",
                p(
                    {
                        "id": {"type": "string"},
                        "new_content": {"type": "string", "minLength": 3},
                        "new_title": {"type": "string"},
                    },
                    ["id", "new_content"],
                ),
                correct,
                "low",
                "obsidian",
                writes=True,
                summarize=lambda a: f"Gedächtnis korrigieren ({a['id'][:8]})",
            ),
            ToolSpec(
                "memory_forget",
                "Löscht eine Erinnerung endgültig (Datei und Index).",
                p({"id": {"type": "string"}}, ["id"]),
                forget,
                "medium",
                "obsidian",
                writes=True,
                summarize=lambda a: f"Erinnerung {a['id'][:8]} endgültig löschen",
            ),
        ]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as e:
        raise ToolUserError("Ungültige ID") from e
