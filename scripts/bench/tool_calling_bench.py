#!/usr/bin/env python3
"""Tool-calling benchmark for local Ollama models.

Sends realistic German Jarvis requests with an OpenAI-style tool list to
Ollama's OpenAI-compatible endpoint and checks whether the model picks the
right tool with the right arguments. Measures latency and resident memory.

Usage:
    python3 scripts/bench/tool_calling_bench.py qwen3.5:4b qwen3:4b [--runs 2] [--ctx 65536]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

OLLAMA = "http://127.0.0.1:11434"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lexware_search_contact",
            "description": "Sucht einen Kunden/Kontakt in Lexware nach Name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lexware_create_invoice_draft",
            "description": "Erstellt einen Rechnungsentwurf in Lexware.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "net_amount_eur": {"type": "number"},
                                "quantity": {"type": "number"},
                            },
                            "required": ["description", "net_amount_eur"],
                        },
                    },
                },
                "required": ["customer_name", "line_items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Legt einen Termin im Google Kalender an.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "ISO-8601 Datum/Zeit"},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_open_app",
            "description": "Startet oder aktiviert eine macOS-Anwendung.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Durchsucht das Langzeitgedächtnis semantisch.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Recherchiert im Web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_send_message",
            "description": "Sendet dem Besitzer eine Telegram-Nachricht.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
]

SYSTEM = (
    "Du bist J.A.R.V.I.S., ein persönlicher Assistent. Nutze Werkzeuge, wenn eine Aktion "
    "nötig ist. Antworte knapp auf Deutsch."
)


@dataclass
class Case:
    name: str
    prompt: str
    expect_tool: str | None
    check: Callable[[dict[str, Any]], bool] = field(default=lambda a: True)
    # A sensible alternative first step (e.g. looking up the customer before invoicing).
    alt_tool: str | None = None
    alt_check: Callable[[dict[str, Any]], bool] = field(default=lambda a: True)


def _has(s: Any, *needles: str) -> bool:
    return isinstance(s, str) and all(n.lower() in s.lower() for n in needles)


CASES: list[Case] = [
    Case("search_contact", "Gibt es den Kunden Müller Gebäudereinigung schon in Lexware?",
         "lexware_search_contact", lambda a: _has(a.get("name"), "müller")),
    Case("invoice_draft", "Bereite eine Rechnung für Müller Gebäudereinigung vor: Website, 499 Euro netto.",
         "lexware_create_invoice_draft",
         lambda a: _has(a.get("customer_name"), "müller")
         and any(abs(float(li.get("net_amount_eur", 0)) - 499) < 0.01 for li in a.get("line_items", [])),
         alt_tool="lexware_search_contact", alt_check=lambda a: _has(a.get("name"), "müller")),
    Case("calendar", "Trag mir morgen um 14 Uhr einen Termin 'Kickoff Müller' für eine Stunde ein. Heute ist der 15.10.2026.",
         "calendar_create_event",
         lambda a: _has(a.get("title"), "kickoff") and _has(a.get("start"), "2026-10-16") and "14" in str(a.get("start"))),
    Case("open_app", "Öffne bitte Safari.", "mac_open_app", lambda a: _has(a.get("app_name"), "safari")),
    Case("memory", "Was weißt du über meine Präferenzen bei Rechnungen?", "memory_search",
         lambda a: _has(a.get("query"), "rechnung")),
    Case("web", "Recherchiere die aktuelle Adresse der Firma Hagebau in Soltau.", "web_search",
         lambda a: _has(a.get("query"), "hagebau")),
    Case("telegram", "Schick mir per Telegram: Backup erledigt.", "telegram_send_message",
         lambda a: _has(a.get("text"), "backup")),
    Case("no_tool", "Wie viel ist 17 mal 23?", None),
    Case("no_tool_smalltalk", "Guten Morgen Jarvis, wie geht's?", None),
    Case("english", "Open Visual Studio Code please.", "mac_open_app", lambda a: _has(a.get("app_name"), "code")),
]


def post(path: str, body: dict[str, Any], timeout: float = 300) -> dict[str, Any]:
    req = urllib.request.Request(
        OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(OLLAMA + path, timeout=30) as r:
        return json.loads(r.read())


def run_case(model: str, case: Case, ctx: int) -> tuple[bool, float, str]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case.prompt}],
        "tools": TOOLS,
        "stream": False,
        "think": False,
        "options": {"num_ctx": ctx, "temperature": 0.2},
    }
    t0 = time.perf_counter()
    resp = post("/api/chat", body)
    dt = time.perf_counter() - t0
    msg = resp.get("message", {})
    calls = msg.get("tool_calls") or []
    if case.expect_tool is None:
        ok = not calls
        return ok, dt, "no call" if ok else f"unexpected {calls[0]['function']['name']}"
    if not calls:
        return False, dt, f"no tool call; text={msg.get('content', '')[:60]!r}"
    fn = calls[0]["function"]
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return False, dt, "invalid JSON args"
    if fn["name"] == case.expect_tool:
        check = case.check
    elif case.alt_tool and fn["name"] == case.alt_tool:
        check = case.alt_check
    else:
        return False, dt, f"wrong tool {fn['name']}"
    try:
        ok = check(args)
    except Exception as e:  # noqa: BLE001 - bench robustness
        ok = False
        return ok, dt, f"check error {e}"
    return ok, dt, "ok" if ok else f"bad args {json.dumps(args, ensure_ascii=False)[:100]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--ctx", type=int, default=65536)
    a = ap.parse_args()
    summary = []
    for model in a.models:
        print(f"\n=== {model} (num_ctx={a.ctx}) ===")
        post("/api/chat", {"model": model, "messages": [{"role": "user", "content": "hi"}],
                           "stream": False, "think": False, "options": {"num_ctx": a.ctx}})  # warm-up
        mem = next((m for m in get("/api/ps").get("models", []) if m["name"] == model), {})
        passed, total, lat = 0, 0, []
        for r in range(a.runs):
            for c in CASES:
                ok, dt, note = run_case(model, c, a.ctx)
                total += 1
                passed += ok
                lat.append(dt)
                print(f"  [{'PASS' if ok else 'FAIL'}] run{r} {c.name:<18} {dt:5.2f}s  {note}")
        size_gb = mem.get("size", 0) / 1e9
        vram_gb = mem.get("size_vram", 0) / 1e9
        summary.append((model, passed, total, statistics.median(lat), max(lat), size_gb, vram_gb))
    print("\n| Modell | Tool-Calling | Median-Latenz | Max-Latenz | Speicher (geladen) |")
    print("|---|---|---|---|---|")
    for m, p, t, med, mx, s, v in summary:
        print(f"| {m} | {p}/{t} ({100 * p / t:.0f} %) | {med:.2f}s | {mx:.2f}s | {s:.1f} GB (GPU {v:.1f} GB) |")


if __name__ == "__main__":
    main()
