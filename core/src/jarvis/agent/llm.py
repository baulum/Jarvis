"""LLM provider adapters.

* ``OllamaClient`` – direct access to the local Ollama server (utility tasks: memory
  extraction, summaries, embeddings, intent parsing). Fast, small prompts, JSON mode.
* ``HermesClient`` – the agent brain. Talks to the Hermes Agent API server through its
  documented REST/SSE surface only (sessions API + capabilities); no Hermes internals.

Both implement small, explicit interfaces so further providers (another local server, a
VPS-hosted model, ...) can be added without touching the orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from jarvis.db import session_scope
from jarvis.db.models import UsageRecord

logger = logging.getLogger(__name__)


async def record_usage(
    provider: str, model: str, purpose: str, prompt_tokens: int, completion_tokens: int, latency_ms: float
) -> None:
    try:
        async with session_scope() as s:
            s.add(
                UsageRecord(
                    provider=provider,
                    model=model,
                    purpose=purpose,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                )
            )
    except Exception:
        logger.debug("usage record failed", exc_info=True)


class OllamaClient:
    def __init__(self, base_url: str, keep_alive: str = "30m") -> None:
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(180, connect=5))

    async def close(self) -> None:
        await self._http.aclose()

    async def version(self) -> str:
        r = await self._http.get("/api/version")
        r.raise_for_status()
        return str(r.json().get("version", ""))

    async def loaded_models(self) -> list[dict[str, Any]]:
        r = await self._http.get("/api/ps")
        r.raise_for_status()
        return list(r.json().get("models", []))

    async def installed_models(self) -> list[dict[str, Any]]:
        r = await self._http.get("/api/tags")
        r.raise_for_status()
        return list(r.json().get("models", []))

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        purpose: str = "utility",
        num_predict: int | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature},
        }
        if num_predict:
            body["options"]["num_predict"] = num_predict
        if json_schema is not None:
            body["format"] = json_schema
        t0 = time.perf_counter()
        r = await self._post_retry("/api/chat", body)
        data = r.json()
        await record_usage(
            "ollama",
            model,
            purpose,
            int(data.get("prompt_eval_count", 0)),
            int(data.get("eval_count", 0)),
            (time.perf_counter() - t0) * 1000,
        )
        return str(data.get("message", {}).get("content", ""))

    async def chat_json(
        self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any], purpose: str = "utility"
    ) -> Any:
        raw = await self.chat(model, messages, json_schema=schema, purpose=purpose)
        return json.loads(raw)

    async def chat_stream(
        self, model: str, messages: list[dict[str, Any]], temperature: float = 0.4, purpose: str = "chat"
    ) -> AsyncIterator[str]:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature},
        }
        t0 = time.perf_counter()
        async with self._http.stream("POST", "/api/chat", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    await record_usage(
                        "ollama",
                        model,
                        purpose,
                        int(chunk.get("prompt_eval_count", 0)),
                        int(chunk.get("eval_count", 0)),
                        (time.perf_counter() - t0) * 1000,
                    )

    async def _post_retry(self, path: str, body: dict[str, Any], attempts: int = 3) -> httpx.Response:
        """POST with retries: under memory pressure Ollama may kill/reload a runner (HTTP 500)."""
        for i in range(attempts):
            try:
                r = await self._http.post(path, json=body)
                if r.status_code < 500 or i == attempts - 1:
                    r.raise_for_status()
                    return r
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if i == attempts - 1:
                    raise
            logger.warning("Ollama %s failed (attempt %d), retrying", path, i + 1)
            await asyncio.sleep(1.5 * (i + 1))
        raise RuntimeError("unreachable")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = await self._post_retry("/api/embed", {"model": model, "input": texts, "keep_alive": self.keep_alive})
        return [list(map(float, v)) for v in r.json()["embeddings"]]

    async def warm(self, model: str) -> None:
        """Load a model into memory and pin it for keep_alive."""
        await self._http.post("/api/generate", json={"model": model, "prompt": "", "keep_alive": self.keep_alive})


@dataclass(slots=True)
class AgentEvent:
    """Normalised event from an agent run."""

    type: str  # delta | tool_started | tool_completed | tool_failed | completed | failed | cancelled | commentary
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(self, api_url: str, api_key: str, timeout_s: float = 300) -> None:
        self.api_url = api_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_s, connect=5),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def health(self) -> bool:
        try:
            r = await self._http.get("/health", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def capabilities(self) -> dict[str, Any]:
        r = await self._http.get("/v1/capabilities", timeout=10)
        r.raise_for_status()
        return dict(r.json())

    async def skills(self) -> list[dict[str, Any]]:
        r = await self._http.get("/v1/skills", timeout=10)
        r.raise_for_status()
        data = r.json()
        return list(data.get("data", data) if isinstance(data, dict) else data)

    async def toolsets(self) -> list[dict[str, Any]]:
        r = await self._http.get("/v1/toolsets", timeout=10)
        r.raise_for_status()
        data = r.json()
        return list(data.get("data", data) if isinstance(data, dict) else data)

    async def create_session(self, title: str) -> str:
        r = await self._http.post("/api/sessions", json={"title": title[:120]})
        if r.status_code >= 400:
            raise HermesError(f"Hermes session create failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        sid = data.get("id") or data.get("session_id") or data.get("session", {}).get("id")
        if not sid:
            raise HermesError(f"Unexpected Hermes session payload: {str(data)[:200]}")
        return str(sid)

    async def stop_run(self, run_id: str) -> None:
        await self._http.post(f"/v1/runs/{run_id}/stop", timeout=10)

    async def chat_stream(
        self, session_id: str, text: str, instructions: str | None = None, images: list[str] | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Run one agent turn in a Hermes session and stream normalised events."""
        payload: dict[str, Any] = {"input": text}
        if instructions:
            payload["instructions"] = instructions
        if images:
            payload["input"] = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}]
                    + [{"type": "input_image", "image_url": img} for img in images],
                }
            ]
        async with self._http.stream("POST", f"/api/sessions/{session_id}/chat/stream", json=payload) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")
                raise HermesError(f"Hermes chat failed: {r.status_code} {body[:300]}")
            event_name = "message"
            data_lines: list[str] = []
            async for line in r.aiter_lines():
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line == "" and data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"raw": raw}
                    ev = self._normalise(event_name if event_name != "message" else data.get("type", ""), data)
                    event_name = "message"
                    if ev is not None:
                        yield ev
                        if ev.type in ("completed", "failed", "cancelled"):
                            return

    @staticmethod
    def _normalise(name: str, data: dict[str, Any]) -> AgentEvent | None:
        if name == "assistant.delta":
            return AgentEvent("delta", text=str(data.get("delta") or data.get("text") or ""), data=data)
        if name == "assistant.commentary":
            return AgentEvent("commentary", text=str(data.get("text", "")), data=data)
        if name == "tool.started":
            return AgentEvent("tool_started", data=data)
        if name == "tool.completed":
            return AgentEvent("tool_completed", data=data)
        if name == "tool.failed":
            return AgentEvent("tool_failed", data=data)
        if name == "assistant.completed":
            return AgentEvent("assistant_completed", text=str(data.get("content") or data.get("text") or ""), data=data)
        if name == "run.completed":
            return AgentEvent("completed", data=data)
        if name == "run.failed":
            return AgentEvent("failed", text=str(data.get("error") or data.get("message") or ""), data=data)
        if name == "run.cancelled":
            return AgentEvent("cancelled", data=data)
        if name in ("run.started", "session.created"):
            return AgentEvent(name.replace(".", "_"), data=data)
        return None
