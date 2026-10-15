"""Built-in playbooks (deterministic multi-step workflows).

A small local model is good at extracting parameters ("Müller Gebäudereinigung, Website,
499 €") but unreliable at planning ten dependent steps. Playbooks give it one tool call
while the engine guarantees order, traceability, approvals and idempotency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.coding.runner import slug
from jarvis.jobs.engine import Playbook, StepContext, StepDef, StepSkip

if TYPE_CHECKING:
    from jarvis.app_state import AppState


def _lexware_ready(ctx: StepContext) -> bool:
    conn = ctx.engine.state.connectors.connectors.get("lexware")
    return conn is not None and conn.active


# ---------------------------------------------------------------- new customer project
async def check_customer(ctx: StepContext) -> dict[str, Any]:
    if not _lexware_ready(ctx):
        raise StepSkip("Lexware ist nicht eingerichtet – Kundenprüfung übersprungen")
    res = await ctx.tool("lexware_search_contact", {"name": ctx.params["customer_name"][:60]})
    contacts = res.get("contacts", [])
    exact = [c for c in contacts if c["name"].strip().lower() == ctx.params["customer_name"].strip().lower()]
    match = exact[0] if exact else (contacts[0] if len(contacts) == 1 else None)
    return {"found": match is not None, "contact": match, "candidates": contacts[:5]}


async def required_fields(ctx: StepContext) -> dict[str, Any]:
    found = ctx.context.get("step_0", {}).get("found")
    if found or ctx.params.get("skip_address"):
        return {"missing": []}
    missing = [f for f in ("street", "zip", "city") if not ctx.params.get(f)]
    if missing and _lexware_ready(ctx):
        labels = {"street": "Straße und Hausnummer", "zip": "PLZ", "city": "Ort"}
        ctx.ask(
            f"Für den neuen Kunden {ctx.params['customer_name']} fehlen Pflichtangaben für die Rechnung: "
            f"{', '.join(labels[m] for m in missing)}. Optional: E-Mail. "
            "(Oder skip_address=true, um ohne Adresse fortzufahren.)",
            missing,
        )
    return {"missing": missing}


async def create_customer(ctx: StepContext) -> dict[str, Any]:
    step0 = ctx.context.get("step_0", {})
    if step0.get("found"):
        return {"contact_id": step0["contact"]["contact_id"], "created": False}
    if not _lexware_ready(ctx):
        raise StepSkip("Lexware ist nicht eingerichtet – Kunde nicht angelegt")
    p = ctx.params
    args: dict[str, Any] = {"company_name": p["customer_name"], "country_code": p.get("country_code", "DE")}
    for k in ("street", "zip", "city", "email", "phone", "contact_first_name", "contact_last_name"):
        if p.get(k):
            args[k] = p[k]
    res = await ctx.tool("lexware_create_contact", args)
    return {"contact_id": res["contact_id"], "created": True, "link": res.get("link")}


async def create_invoice(ctx: StepContext) -> dict[str, Any]:
    contact_id = ctx.context.get("step_2", {}).get("contact_id")
    if not contact_id:
        raise StepSkip("Kein Lexware-Kunde vorhanden – Rechnungsentwurf übersprungen")
    p = ctx.params
    res = await ctx.tool(
        "lexware_create_invoice_draft",
        {
            "contact_id": contact_id,
            "line_items": [
                {
                    "name": p.get("project_type", "Website"),
                    "amount_eur": float(p["price_eur"]),
                    "quantity": 1,
                    "unit_name": "Pauschal",
                    **({"description": p["description"]} if p.get("description") else {}),
                }
            ],
            "title": "Rechnung",
        },
    )
    return res


async def create_project(ctx: StepContext) -> dict[str, Any]:
    p = ctx.params
    name = f"{p['customer_name']} {p.get('project_type', 'Website')}"
    res = await ctx.tool("project_create", {"name": name})
    return {"path": res["path"], "slug": slug(name, 50)}


async def write_brief(ctx: StepContext) -> dict[str, Any]:
    from pathlib import Path

    p = ctx.params
    path = Path(ctx.context["step_4"]["path"])
    brief = f"""# Projektauftrag: {p.get("project_type", "Website")} für {p["customer_name"]}

## Kunde
- Firma: {p["customer_name"]}
- Branche/Beschreibung: {p.get("description") or "nicht angegeben – aus dem Firmennamen ableiten"}
- Budget: {p["price_eur"]} EUR

## Anforderungen
{
        p.get("requirements")
        or '''- Moderne, schnelle, responsive One-Page-Website (Deutsch)
- Abschnitte: Hero mit klarer Botschaft, Leistungen, Über uns, Referenzen/Vertrauen, Kontakt
- Kontaktbereich mit Telefon/E-Mail-Platzhaltern (keine erfundenen Kontaktdaten)
- Impressum- und Datenschutz-Seiten als Platzhalter mit TODO-Hinweisen
- SEO-Grundlagen: Title, Meta-Description, semantisches HTML, Open Graph
- Barrierearm (Kontraste, Alt-Texte, Tastaturbedienung)'''
    }

## Technik
- Vite + TypeScript, statisch deploybar; `npm run build` muss fehlerfrei durchlaufen
- Keine externen Tracker; Bilder als optimierte Platzhalter
"""
    (path / "BRIEF.md").write_text(brief, encoding="utf-8")
    return {"brief_path": str(path / "BRIEF.md")}


async def start_coding(ctx: StepContext) -> dict[str, Any]:
    if ctx.context.get("step_6", {}).get("job_id"):
        return ctx.context["step_6"]
    path = ctx.context["step_4"]["path"]
    res = await ctx.tool(
        "coding_start_job",
        {
            "title": f"Website {ctx.params['customer_name']}",
            "instructions": "Setze den Projektauftrag in BRIEF.md vollständig um. Initialisiere das Projekt mit Vite "
            "(vanilla-ts) in diesem Verzeichnis, installiere Abhängigkeiten und stelle sicher, dass "
            "`npm run build` erfolgreich ist.",
            "project_path": path,
            "test_command": "npm run build",
        },
    )
    return {"job_id": res["job_id"]}


async def await_tests(ctx: StepContext) -> dict[str, Any]:
    job_id = ctx.context["step_6"]["job_id"]
    ev = ctx.context.get("event_7")
    if ev is None:
        ctx.wait_for(
            "coding.finished",
            {"job_id": job_id},
            "Wartet auf Abschluss der Website-Entwicklung und der Tests (Claude Code)",
        )
    status = await ctx.tool("coding_job_status", {"job_id": job_id})
    tests = (status.get("result") or {}).get("tests") or {}
    return {
        "job_status": status["status"],
        "tests_passed": tests.get("passed"),
        "branch": status.get("branch"),
        "worktree": status.get("worktree"),
        "summary": (status.get("result") or {}).get("summary", "")[:800],
    }


async def update_status(ctx: StepContext) -> dict[str, Any]:
    p = ctx.params
    c = ctx.context
    inv = c.get("step_3", {})
    dev = c.get("step_7", {})
    content = (
        f"Projekt {p.get('project_type', 'Website')} für [[{p['customer_name']}]] zum Festpreis von "
        f"{p['price_eur']} EUR. Projektordner: {c.get('step_4', {}).get('path')}. "
        f"Rechnungsentwurf: {inv.get('number') or inv.get('invoice_id') or 'nicht erstellt'}. "
        f"Entwicklung: {dev.get('job_status', 'offen')}, Tests "
        f"{'bestanden' if dev.get('tests_passed') else 'nicht bestanden/offen'}; Branch {dev.get('branch')}."
    )
    await ctx.tool(
        "memory_save",
        {
            "title": f"Projekt {p['customer_name']} {p.get('project_type', 'Website')}",
            "content": content,
            "kind": "project",
            "entities": [p["customer_name"]],
        },
    )
    await ctx.tool(
        "memory_save",
        {
            "title": p["customer_name"],
            "kind": "customer",
            "entities": [],
            "content": f"Kunde {p['customer_name']}. Auftrag: {p.get('project_type', 'Website')} "
            f"für {p['price_eur']} EUR.",
        },
    )
    ctx.context["summary"] = content
    return {"note": content}


async def inform_owner(ctx: StepContext) -> dict[str, Any]:
    warnings = ctx.context.get("_warnings") or []
    body = ctx.context.get("summary", "")
    if warnings:
        body += "\nHinweise: " + "; ".join(warnings)
    await ctx.tool(
        "notify_owner",
        {
            "title": f"Neukunde {ctx.params['customer_name']} eingerichtet",
            "body": body,
            "channels": ["desktop", "telegram"],
        },
    )
    return {"notified": True}


NEW_CUSTOMER_PROJECT = Playbook(
    name="new_customer_project",
    title="Neukunde + Rechnung + Projekt",
    title_template="Neukunde {customer_name}: {project_type} ({price_eur} EUR)",
    description="Prüft/legt den Kunden in Lexware an, erstellt einen Rechnungsentwurf, legt das Projekt an, "
    "lässt es mit Claude Code entwickeln, testet, aktualisiert das Gedächtnis und informiert Sir.",
    params_schema={
        "type": "object",
        "properties": {
            "customer_name": {"type": "string", "minLength": 2},
            "project_type": {"type": "string", "default": "Website"},
            "price_eur": {"type": "number", "minimum": 0},
            "description": {"type": "string"},
            "requirements": {"type": "string"},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "street": {"type": "string"},
            "zip": {"type": "string"},
            "city": {"type": "string"},
            "country_code": {"type": "string", "pattern": "^[A-Z]{2}$"},
            "contact_first_name": {"type": "string"},
            "contact_last_name": {"type": "string"},
            "skip_address": {"type": "boolean"},
        },
        "required": ["customer_name", "price_eur"],
    },
    steps=[
        StepDef("Vorhandene Kundendaten prüfen", check_customer),
        StepDef("Fehlende Pflichtangaben ermitteln", required_fields),
        StepDef("Kunden in Lexware anlegen", create_customer),
        StepDef("Rechnungsentwurf erstellen", create_invoice),
        StepDef("Projektverzeichnis anlegen", create_project),
        StepDef("Entwicklungsauftrag vorbereiten", write_brief),
        StepDef("Website mit Claude Code entwickeln", start_coding),
        StepDef("Tests durchführen", await_tests),
        StepDef("Projektstatus aktualisieren", update_status),
        StepDef("Sir informieren", inform_owner),
    ],
)


# ---------------------------------------------------------------- generic tool sequence
async def run_custom_step(ctx: StepContext) -> dict[str, Any]:
    steps = ctx.params["steps"]
    i = ctx.context.get("_custom_index", 0)
    outputs = ctx.context.setdefault("_custom_outputs", [])
    while i < len(steps):
        step = steps[i]
        data = await ctx.tool(step["tool"], step.get("args", {}), key=f"custom{i}")
        outputs.append({"tool": step["tool"], "result": data})
        i += 1
        ctx.context["_custom_index"] = i
    ctx.context["summary"] = f"{len(steps)} Schritte ausgeführt"
    return {"outputs": outputs}


CUSTOM_SEQUENCE = Playbook(
    name="tool_sequence",
    title="Werkzeug-Abfolge",
    description="Führt eine Liste von Werkzeugaufrufen nacheinander aus (mit Freigaben und Wiederaufnahme).",
    params_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
                    "required": ["tool"],
                },
            },
        },
        "required": ["steps"],
    },
    steps=[StepDef("Werkzeuge ausführen", run_custom_step)],
)


def register_playbooks(state: AppState) -> None:
    state.tasks.register(NEW_CUSTOMER_PROJECT)
    state.tasks.register(CUSTOM_SEQUENCE)
