"""Lexware Office (formerly lexoffice) — official public REST API (https://api.lexware.io/v1).

Checked: no official Lexware MCP server exists (only community projects), so Jarvis uses the
documented API directly. Notes from the API docs that shape the tool design:
* invoices are created as drafts by default; the status of an existing invoice can NOT be
  changed via the API → finalizing happens in Lexware (deeplink) or by creating a finalized
  invoice directly (critical risk);
* the API cannot send e-mails → ``lexware_send_invoice`` downloads the PDF and sends it via
  the Gmail connector (high risk, needs approval);
* rate limit: 2 requests/second.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import httpx

from jarvis.agent.persona import TZ
from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

API = "https://api.lexware.io"
APP = "https://app.lexware.de"


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class _RateLimit:
    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.interval:
                await asyncio.sleep(self.interval - delta)
            self._last = time.monotonic()


class LexwareConnector(Connector):
    manifest = Manifest(
        id="lexware", name="Lexware Office",
        description="Kunden suchen/anlegen, Rechnungsentwürfe erstellen, Rechnungen abrufen, Status prüfen und "
                    "nach Freigabe versenden.",
        category="business", auth="api_key",
        secrets=[SecretField("lexware-api-key", "Lexware Public API Key",
                             help="In Lexware Office: Einstellungen › Erweiterungen › Public API › Schlüssel erzeugen.")],
        settings_schema={"type": "object", "properties": {
            "default_tax_rate": {"type": "number", "enum": [0, 7, 19], "default": 19},
            "tax_type": {"type": "string", "enum": ["net", "gross", "vatfree"], "default": "net"},
            "payment_term_days": {"type": "integer", "minimum": 0, "maximum": 120, "default": 14},
            "invoice_intro": {"type": "string", "default": "Vielen Dank für Ihren Auftrag. Wir berechnen Ihnen:"},
            "invoice_remark": {"type": "string", "default": "Bitte überweisen Sie den Betrag innerhalb des Zahlungsziels."},
        }},
        docs_url="https://developers.lexware.io/docs/",
        mcp_note="Kein offizieller Lexware-MCP-Server vorhanden (nur Community-Projekte); direkte Nutzung der "
                 "offiziellen Public API.",
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._rl = _RateLimit(2.0)

    async def _req(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                   json: dict[str, Any] | None = None, raw: bool = False) -> Any:
        key = self.secret("lexware-api-key")
        if not key:
            raise ToolUserError("Lexware ist nicht eingerichtet (API-Key fehlt).")
        for attempt in range(3):
            await self._rl.wait()
            async with httpx.AsyncClient(base_url=API, timeout=30) as c:
                r = await c.request(method, path, params=params, json=json, headers={
                    "Authorization": f"Bearer {key}", "Accept": "application/pdf" if raw else "application/json",
                    "Content-Type": "application/json"})
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break
        if r.status_code >= 400:
            detail = r.text[:400]
            if r.status_code == 401:
                raise ToolUserError("Lexware lehnt den API-Key ab (401).")
            raise ToolUserError(f"Lexware-Fehler {r.status_code}: {detail}")
        return r.content if raw else (r.json() if r.content else {})

    async def health(self) -> Health:
        try:
            prof = await self._req("GET", "/v1/profile")
            return Health("ok", f"Verbunden: {prof.get('companyName', 'Lexware')}",
                          details={"organizationId": prof.get("organizationId")})
        except ToolUserError as e:
            return Health("error", str(e))

    def tools(self) -> list[ToolSpec]:
        S = "lexware"

        async def search(ctx: ToolContext, a: dict[str, Any]) -> Any:
            params: dict[str, Any] = {"size": 10}
            if a.get("name"):
                if len(a["name"]) < 3:
                    raise ToolUserError("Name muss mindestens 3 Zeichen haben")
                params["name"] = a["name"]
            if a.get("email"):
                params["email"] = a["email"]
            if len(params) == 1:
                raise ToolUserError("name oder email angeben")
            data = await self._req("GET", "/v1/contacts", params=params)
            return {"count": data.get("totalElements", 0),
                    "contacts": [_contact_summary(c) for c in data.get("content", [])]}

        async def get_contact(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return _contact_summary(await self._req("GET", f"/v1/contacts/{a['contact_id']}"), full=True)

        async def create_contact(ctx: ToolContext, a: dict[str, Any]) -> Any:
            body: dict[str, Any] = {"version": 0, "roles": {"customer": {}}}
            if a.get("company_name"):
                company: dict[str, Any] = {"name": a["company_name"]}
                if a.get("contact_last_name"):
                    company["contactPersons"] = [{
                        "firstName": a.get("contact_first_name", ""), "lastName": a["contact_last_name"],
                        "primary": True, **({"emailAddress": a["email"]} if a.get("email") else {})}]
                body["company"] = company
            elif a.get("last_name"):
                body["person"] = {"firstName": a.get("first_name", ""), "lastName": a["last_name"]}
            else:
                raise ToolUserError("company_name oder last_name ist erforderlich")
            if a.get("street") or a.get("city"):
                body["addresses"] = {"billing": [{k2: a[k1] for k1, k2 in
                                                  (("street", "street"), ("zip", "zip"), ("city", "city"))
                                                  if a.get(k1)} | {"countryCode": a.get("country_code", "DE")}]}
            if a.get("email"):
                body["emailAddresses"] = {"business": [a["email"]]}
            if a.get("phone"):
                body["phoneNumbers"] = {"business": [a["phone"]]}
            if a.get("note"):
                body["note"] = a["note"]
            res = await self._req("POST", "/v1/contacts", json=body)
            return {"contact_id": res.get("id"), "link": f"{APP}/permalink/contacts/view/{res.get('id')}"}

        async def create_invoice(ctx: ToolContext, a: dict[str, Any], finalize: bool = False) -> Any:
            st = self.settings
            tax_type = st.get("tax_type", "net")
            rate = st.get("default_tax_rate", 19)
            items = []
            for li in a["line_items"]:
                price: dict[str, Any] = {"currency": "EUR",
                                         "taxRatePercentage": li.get("tax_rate", 0 if tax_type == "vatfree" else rate)}
                price["grossAmount" if tax_type == "gross" else "netAmount"] = round(float(li["amount_eur"]), 2)
                items.append({"type": "custom", "name": li["name"][:255],
                              **({"description": li["description"]} if li.get("description") else {}),
                              "quantity": li.get("quantity", 1), "unitName": li.get("unit_name", "Stück"),
                              "unitPrice": price, "discountPercentage": 0})
            if a.get("contact_id"):
                address: dict[str, Any] = {"contactId": a["contact_id"]}
            elif a.get("customer_name"):
                address = {"name": a["customer_name"], "countryCode": a.get("country_code", "DE"),
                           **{k: a[k] for k in ("street", "zip", "city") if a.get(k)}}
            else:
                raise ToolUserError("contact_id oder customer_name angeben")
            now = datetime.now(TZ)
            body = {
                "archived": False, "voucherDate": now.isoformat(timespec="milliseconds"), "address": address,
                "lineItems": items, "totalPrice": {"currency": "EUR"}, "taxConditions": {"taxType": tax_type},
                "paymentConditions": {"paymentTermLabel": f"Zahlbar innerhalb von {st.get('payment_term_days', 14)} "
                                                          "Tagen ohne Abzug",
                                      "paymentTermDuration": st.get("payment_term_days", 14)},
                "shippingConditions": {"shippingType": "service",
                                       "shippingDate": (a.get("service_date") or now.isoformat(timespec="milliseconds"))},
                "title": a.get("title", "Rechnung"),
                "introduction": a.get("introduction") or st.get("invoice_intro", ""),
                "remark": a.get("remark") or st.get("invoice_remark", ""),
            }
            res = await self._req("POST", "/v1/invoices", params={"finalize": "true"} if finalize else None,
                                  json=body)
            inv = await self._req("GET", f"/v1/invoices/{res['id']}")
            return {"invoice_id": res["id"], "status": inv.get("voucherStatus"),
                    "number": inv.get("voucherNumber"), "total": inv.get("totalPrice", {}),
                    "link": f"{APP}/permalink/invoices/edit/{res['id']}"}

        async def create_draft(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await create_invoice(ctx, a, finalize=False)

        async def create_final(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await create_invoice(ctx, a, finalize=True)

        async def get_invoice(ctx: ToolContext, a: dict[str, Any]) -> Any:
            inv = await self._req("GET", f"/v1/invoices/{a['invoice_id']}")
            return {"invoice_id": inv.get("id"), "status": inv.get("voucherStatus"), "number": inv.get("voucherNumber"),
                    "date": inv.get("voucherDate"), "due": inv.get("dueDate"), "customer": inv.get("address", {}).get("name"),
                    "total": inv.get("totalPrice", {}), "lines": [
                        {"name": li.get("name"), "qty": li.get("quantity"), "net": li.get("unitPrice", {}).get("netAmount")}
                        for li in inv.get("lineItems", []) if li.get("type") != "text"],
                    "link": f"{APP}/permalink/invoices/view/{inv.get('id')}"}

        async def list_invoices(ctx: ToolContext, a: dict[str, Any]) -> Any:
            status = a.get("status") or "draft,open,overdue"
            data = await self._req("GET", "/v1/voucherlist", params={
                "voucherType": "invoice", "voucherStatus": status, "size": min(int(a.get("limit", 20)), 50),
                "sort": "voucherDate,DESC"})
            return [{"invoice_id": v.get("id"), "number": v.get("voucherNumber"), "status": v.get("voucherStatus"),
                     "date": v.get("voucherDate"), "due": v.get("dueDate"), "customer": v.get("contactName"),
                     "total": v.get("totalAmount"), "open": v.get("openAmount")} for v in data.get("content", [])]

        async def send_invoice(ctx: ToolContext, a: dict[str, Any]) -> Any:
            inv = await self._req("GET", f"/v1/invoices/{a['invoice_id']}")
            if inv.get("voucherStatus") == "draft":
                raise ToolUserError("Die Rechnung ist noch ein Entwurf. Bitte zuerst in Lexware finalisieren: "
                                    f"{APP}/permalink/invoices/edit/{a['invoice_id']}")
            pdf = await self._req("GET", f"/v1/invoices/{a['invoice_id']}/file", raw=True)
            gmail = self.state.connectors.connectors.get("google")
            if gmail is None or not gmail.active:
                raise ToolUserError("Zum Versand wird der Google-Connector (Gmail) benötigt.")
            number = inv.get("voucherNumber", "Rechnung")
            sent = await gmail.send_mail(  # type: ignore[attr-defined]
                to=a["to_email"], subject=a.get("subject") or f"Rechnung {number}",
                body=a.get("message") or f"Guten Tag,\n\nanbei erhalten Sie die Rechnung {number}.\n\nMit freundlichen Grüßen",
                attachments=[(f"{number}.pdf", "application/pdf", pdf)])
            return {"sent": True, "number": number, "gmail_message_id": sent.get("id")}

        line_items = {"type": "array", "minItems": 1, "items": _obj({
            "name": {"type": "string"}, "description": {"type": "string"},
            "quantity": {"type": "number", "exclusiveMinimum": 0}, "unit_name": {"type": "string"},
            "amount_eur": {"type": "number", "minimum": 0}, "tax_rate": {"type": "number", "enum": [0, 7, 19]}},
            ["name", "amount_eur"])}
        inv_props = {"contact_id": {"type": "string"}, "customer_name": {"type": "string"},
                     "street": {"type": "string"}, "zip": {"type": "string"}, "city": {"type": "string"},
                     "country_code": {"type": "string", "pattern": "^[A-Z]{2}$"},
                     "line_items": line_items, "title": {"type": "string"}, "introduction": {"type": "string"},
                     "remark": {"type": "string"}, "service_date": {"type": "string"}}

        def inv_summary(a: dict[str, Any], kind: str) -> str:
            total = sum(float(li["amount_eur"]) * float(li.get("quantity", 1)) for li in a["line_items"])
            who = a.get("customer_name") or a.get("contact_id", "?")
            return f"{kind} für {who}: {', '.join(li['name'] for li in a['line_items'])} – {total:.2f} EUR netto"

        return [
            ToolSpec("lexware_search_contact", "Sucht Kunden in Lexware nach Name (mind. 3 Zeichen) oder E-Mail.",
                     _obj({"name": {"type": "string"}, "email": {"type": "string"}}), search, "read", S),
            ToolSpec("lexware_get_contact", "Liest einen Lexware-Kontakt vollständig.",
                     _obj({"contact_id": {"type": "string"}}, ["contact_id"]), get_contact, "read", S),
            ToolSpec("lexware_create_contact", "Legt einen Kunden in Lexware an (Firma oder Person).",
                     _obj({"company_name": {"type": "string"}, "first_name": {"type": "string"},
                           "last_name": {"type": "string"}, "contact_first_name": {"type": "string"},
                           "contact_last_name": {"type": "string"}, "street": {"type": "string"},
                           "zip": {"type": "string"}, "city": {"type": "string"},
                           "country_code": {"type": "string", "pattern": "^[A-Z]{2}$"},
                           "email": {"type": "string"}, "phone": {"type": "string"}, "note": {"type": "string"}}),
                     create_contact, "medium", S, writes=True,
                     summarize=lambda a: f"Lexware-Kunde anlegen: {a.get('company_name') or a.get('last_name')}"
                                         f"{', ' + a['city'] if a.get('city') else ''}"),
            ToolSpec("lexware_create_invoice_draft", "Erstellt einen Rechnungsentwurf (nicht finalisiert, nicht versendet).",
                     _obj(inv_props, ["line_items"]), create_draft, "medium", S, writes=True,
                     summarize=lambda a: inv_summary(a, "Rechnungsentwurf")),
            ToolSpec("lexware_create_final_invoice", "Erstellt eine sofort finalisierte Rechnung (unwiderruflich, "
                     "erhält Rechnungsnummer).", _obj(inv_props, ["line_items"]), create_final, "critical", S,
                     writes=True, summarize=lambda a: inv_summary(a, "FINALE Rechnung")),
            ToolSpec("lexware_get_invoice", "Liest eine Rechnung inkl. Status (draft/open/paid/overdue/voided).",
                     _obj({"invoice_id": {"type": "string"}}, ["invoice_id"]), get_invoice, "read", S),
            ToolSpec("lexware_list_invoices", "Listet Rechnungen nach Status (Komma-Liste: draft,open,paid,paidoff,"
                     "voided,overdue).", _obj({"status": {"type": "string"}, "limit": {"type": "integer"}}),
                     list_invoices, "read", S),
            ToolSpec("lexware_send_invoice", "Versendet eine finalisierte Rechnung als PDF per E-Mail (Gmail).",
                     _obj({"invoice_id": {"type": "string"}, "to_email": {"type": "string", "format": "email"},
                           "subject": {"type": "string"}, "message": {"type": "string"}}, ["invoice_id", "to_email"]),
                     send_invoice, "high", S, writes=True, timeout_s=90,
                     summarize=lambda a: f"Rechnung {a['invoice_id'][:8]} per E-Mail an {a['to_email']} senden"),
        ]


def _contact_summary(c: dict[str, Any], full: bool = False) -> dict[str, Any]:
    company = c.get("company") or {}
    person = c.get("person") or {}
    billing = (c.get("addresses") or {}).get("billing") or [{}]
    out = {"contact_id": c.get("id"), "name": company.get("name") or
           f"{person.get('firstName', '')} {person.get('lastName', '')}".strip(),
           "customer_number": (c.get("roles", {}).get("customer") or {}).get("number"),
           "email": ((c.get("emailAddresses") or {}).get("business") or [None])[0],
           "address": {k: billing[0].get(k) for k in ("street", "zip", "city", "countryCode") if billing[0].get(k)}}
    if full:
        out["phone"] = ((c.get("phoneNumbers") or {}).get("business") or [None])[0]
        out["contact_persons"] = company.get("contactPersons", [])
        out["note"] = c.get("note")
        out["link"] = f"{APP}/permalink/contacts/view/{c.get('id')}"
    return out
