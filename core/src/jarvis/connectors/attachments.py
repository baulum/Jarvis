"""Chat-Anhänge lesen (Werkzeug ``attachment_read``).

Text, Markdown, CSV, JSON und Word stehen schon in Sirs Nachricht; hier liest der Agent den Rest eines langen
Dokuments seitenweise (``offset``), PDFs und große Bilder. PDFs und Bilder liest eine einzelne Claude-Anfrage mit
dem Read-Werkzeug – beschränkt auf den Anhangsordner (``ClaudeOneShot.run(read_dir=…)``). Jedes Ergebnis ist
eingezäunt als nicht vertrauenswürdiger Inhalt; Treffer der Injection-Erkennung markieren die Sitzung.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from jarvis.agent.attachments import (
    NAME_PATTERN,
    READ_CHARS,
    AttachmentError,
    attachments_dir,
    extract_text,
    format_size,
    load_attachment,
)
from jarvis.agent.claude_agent import ClaudeUnavailable
from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

_PDF_PROMPT = (
    "Lies die PDF-Datei {path} vollständig und gib ihren Inhalt als Text wieder: Überschriften, Absätze, "
    "Tabellen als Markdown, Zahlen exakt. Keine Einleitung, keine Bewertung."
)
_IMAGE_PROMPT = (
    "Lies die Bilddatei {path} und beschreibe auf Deutsch genau, was zu sehen ist. Gib jeden sichtbaren Text "
    "wörtlich wieder."
)
_CACHE_MAX = 32


class AttachmentsConnector(Connector):
    manifest = Manifest(
        id="attachments",
        name="Chat-Anhänge",
        description="Liest Dateien, die Sir im Chat anhängt: PDF, Word, Text, Markdown, CSV, JSON und Bilder.",
        category="system",
        auth="none",
        default_enabled=True,
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._read_cache: OrderedDict[tuple[str, str], str] = OrderedDict()

    async def health(self) -> Health:
        return Health("ok", "Anhänge werden lokal gelesen")

    async def _via_claude(self, prompt: str, file: str, question: str) -> str:
        key = (file, question)
        if key in self._read_cache:
            self._read_cache.move_to_end(key)
            return self._read_cache[key]
        if question:
            prompt += f" Beantworte danach diese Frage dazu: {question}"
        try:
            text = str(await self.state.oneshot.run(prompt, read_dir=attachments_dir(self.state), timeout_s=240))
        except ClaudeUnavailable as e:
            raise ToolUserError(f"Der Anhang konnte nicht gelesen werden: {e}") from e
        self._read_cache[key] = text
        while len(self._read_cache) > _CACHE_MAX:
            self._read_cache.popitem(last=False)
        return text

    async def read(self, ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
        try:
            info = load_attachment(attachments_dir(self.state), str(a["file"]))
        except AttachmentError as e:
            raise ToolUserError(str(e)) from e
        question = str(a.get("question") or "").strip()[:500]
        offset = int(a.get("offset") or 0)
        if info.ext == "pdf" or info.kind == "image":
            template = _PDF_PROMPT if info.ext == "pdf" else _IMAGE_PROMPT
            text = await self._via_claude(template.format(path=info.path), info.file, question)
        else:
            try:
                text = extract_text(info)
            except AttachmentError as e:
                raise ToolUserError(str(e)) from e
        part = text[offset : offset + READ_CHARS]
        wrapped = wrap_untrusted(part, f"Anhang {info.name}", READ_CHARS)
        if wrapped["suspicious_patterns"] and ctx.session_id:
            await self.state.executor.taint(ctx.session_id, "attachment_read")
        out: dict[str, Any] = {
            "name": info.name,
            "type": info.type.label,
            "size": format_size(info.size),
            "notice": wrapped["notice"],
            "content": wrapped["content"],
            "chars": len(text),
        }
        if offset + READ_CHARS < len(text):
            out["next_offset"] = offset + READ_CHARS
        return out

    def tools(self) -> list[ToolSpec]:
        async def attachment_read(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.read(ctx, a)

        return [
            ToolSpec(
                "attachment_read",
                "Liest einen Anhang, den Sir im Chat geschickt hat (file = gespeicherter Name aus <anhaenge>). "
                "Für PDFs, zu große Bilder und den Rest langer Dokumente (offset). Text, Word und kleine Bilder "
                "stehen bereits in der Nachricht.",
                {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "pattern": NAME_PATTERN},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                        "question": {
                            "type": "string",
                            "maxLength": 500,
                            "description": "Optionale Frage zum Inhalt (bei PDFs und Bildern)",
                        },
                    },
                    "required": ["file"],
                    "additionalProperties": False,
                },
                attachment_read,
                "read",
                "attachments",
                timeout_s=300,
                summarize=lambda a: f"Anhang lesen: {a.get('file', '')}",
            )
        ]
