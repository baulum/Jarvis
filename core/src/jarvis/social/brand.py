"""Brand facts, offers and the inspiration formats.

A ``Brand`` is built per company profile (``companies.py``): the profile's fields plus, if it has one, a fact file
from ``config/social/`` (PJE Systems: ``brand.yaml`` – claims, facts, references; reloaded on every run so Sir's
edits apply to the next video without a restart).

Sir's input for one production (the "Produzieren" field, ``hint``) has priority over every default:
``Brand.with_hint`` turns prices in it into the only valid prices (a price fact for the ``fact`` scene, the offer
list, what captions and the QA accept); profile prices that differ are dropped instead of competing with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from jarvis.config import CONFIG_DIR
from jarvis.social.prices import PRICE_RE, amount, amounts_in, prices_in

SOCIAL_CONFIG = CONFIG_DIR / "social"
HINT_FACT = "hint_price"
_DANGLING = {"für", "ab", "zu", "um", "nur", "von", "statt", "je", "à", "zum", "für nur", "mit", "bei"}


@dataclass(slots=True)
class Brand:
    raw: dict[str, Any]
    hint: str = ""

    # ------------------------------------------------------------------ identity
    @property
    def company(self) -> str:
        return str(self.raw.get("company", "PJE Systems"))

    @property
    def company_id(self) -> str:
        return str(self.raw.get("company_id") or "")

    @property
    def description(self) -> str:
        return str(self.raw.get("description") or "")

    @property
    def website(self) -> str:
        return str(self.raw.get("website", "pje-systems.de"))

    @property
    def cta_button(self) -> str:
        return str((self.raw.get("cta") or {}).get("default_button", "Projekt anfragen"))

    @property
    def colors(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.raw.get("colors") or {}).items() if v}

    @property
    def hashtags(self) -> list[str]:
        return [str(t) for t in self.raw.get("hashtags") or []]

    def render_info(self) -> dict[str, Any]:
        """What Remotion needs to draw the brand (``Storyboard.brand`` in social/src/types.ts)."""
        out: dict[str, Any] = {"name": self.company, "colors": self.colors}
        logo = {k: str(v) for k, v in (self.raw.get("logo") or {}).items() if k in ("on_dark", "on_light") and v}
        if logo:
            out["logo"] = logo
        return out

    # ------------------------------------------------------------------ Sir's input wins
    def with_hint(self, hint: str) -> Brand:
        return Brand(self.raw, (hint or "").strip())

    @property
    def hint_prices(self) -> list[str]:
        """Prices in Sir's input, in the order he wrote them (one entry per amount)."""
        seen: dict[str, str] = {}
        for m in PRICE_RE.finditer(self.hint):
            seen.setdefault(amount(m.group(0)), m.group(0).strip())
        return [_norm_price(p) for a, p in seen.items() if a]

    def _hint_overrides(self, text: str) -> bool:
        """True if a default text names a price that Sir's input does not – it must give way to the input."""
        own = amounts_in(text)
        return bool(self.hint_prices) and bool(own) and not own <= {amount(p) for p in self.hint_prices}

    def _hint_label(self) -> str:
        """The clause of Sir's input around the (first) price, short enough for the fact scene."""
        parts = [p.strip() for p in re.split(r"[;!?\n]|\.(?!\d)", self.hint) if p.strip()]
        clause = next((p for p in parts if prices_in(p)), self.hint)
        # the value is shown big above the label – "Angebot für 299 €" becomes the label "Angebot"
        clause = re.sub(r"\s+", " ", PRICE_RE.sub(" ", clause)).strip(" ,:–-")
        words = clause.split()
        while words and words[-1].lower() in _DANGLING:
            words.pop()
        clause = " ".join(words) or "Angebot"
        if len(clause) > 60:
            clause = clause[:60].rsplit(" ", 1)[0]
        return clause[:1].upper() + clause[1:]

    # ------------------------------------------------------------------ facts and offers
    @property
    def facts(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for i, p in enumerate(self.hint_prices):
            out.append({"id": HINT_FACT if i == 0 else f"{HINT_FACT}_{i + 1}", "value": p, "label": self._hint_label()})
        for f in self.raw.get("facts") or []:
            if not (f.get("id") and f.get("value") and f.get("label")):
                continue
            if self._hint_overrides(f"{f['value']} {f['label']}"):
                continue
            out.append({"id": str(f["id"]), "value": str(f["value"]), "label": str(f["label"])})
        return out

    @property
    def fact_ids(self) -> set[str]:
        return {f["id"] for f in self.facts}

    def active_offers(self, today: date | None = None) -> list[str]:
        today = today or date.today()
        out = [f"Vorgabe von Sir: {self.hint}"] if self.hint_prices else []
        for o in self.raw.get("offers") or []:
            if not o.get("active"):
                continue
            until = o.get("valid_until")
            if until and date.fromisoformat(str(until)) < today:
                continue
            if self._hint_overrides(str(o["text"])):
                continue
            out.append(str(o["text"]))
        return out

    def allowed_prices(self, today: date | None = None) -> set[str]:
        """Every price a video or caption may name: Sir's input, else the valid offers and the facts."""
        texts = self.active_offers(today) + [f["value"] + " " + f["label"] for f in self.facts]
        return {p for t in texts for p in prices_in(t)}

    def required_prices(self) -> list[str]:
        """Prices from Sir's input – they have to show up in the video and in the captions."""
        return self.hint_prices

    def allowed_references(self) -> list[dict[str, str]]:
        return [{"name": r["name"], "url": r["url"]} for r in self.raw.get("references") or [] if r.get("allowed")]

    def priority_block(self) -> str:
        """Sir's input at the top of every prompt: it overrides profile defaults (price, offer, topic)."""
        if not self.hint:
            return ""
        lines = [
            "VORRANG – VORGABE VON SIR FÜR DIESES VIDEO (gilt vor allen Standardwerten des Profils, auch vor "
            "hinterlegten Preisen und Angeboten; Thema, Angebot und Preis daraus wörtlich übernehmen):",
            self.hint,
        ]
        if self.hint_prices:
            lines.append(
                f"Preis laut Vorgabe: {' / '.join(self.hint_prices)}. Genau diesen Preis zeigen und nennen – "
                "keinen anderen Preis aus dem Profil (frühere Standardpreise gelten für dieses Video nicht)."
            )
        return "\n".join(lines)

    def prompt_block(self, today: date | None = None) -> str:
        r = self.raw
        refs = self.allowed_references()
        head = f"Firma: {self.company}"
        if r.get("owner"):
            head += f" ({r['owner']})"
        if r.get("location"):
            head += f", {r['location']}"
        if r.get("region"):
            head += f"; Einsatzgebiet: {r['region']}"
        lines = [head]
        if self.description:
            lines.append(f"Positionierung: {self.description}")
        if r.get("audience"):
            lines.append(f"Zielgruppe: {r['audience']}")
        lines.append(f"Website/CTA: {self.cta_button} → {self.website}")
        if r.get("services"):
            lines.append("Leistungen: " + "; ".join(r.get("services") or []))
        if r.get("claims"):
            lines.append("Belegte Claims (wörtlich von der Website): " + " | ".join(r.get("claims") or []))
        lines += [
            "Fakten für die Szene `fact` (nur diese IDs): "
            + (
                "; ".join(f"{f['id']} = {f['value']} ({f['label']})" for f in self.facts)
                or "keine – Szene fact nicht verwenden"
            ),
            "Gültige Angebote (nur diese Preise/Angebote nennen): " + ("; ".join(self.active_offers(today)) or "keine"),
            "Echte Kundenprojekte, die gezeigt werden dürfen: "
            + (
                ", ".join(f"{x['name']} ({x['url']})" for x in refs) or "keine – keine echten Kunden zeigen oder nennen"
            ),
            "Tonalität: " + " ".join(r.get("voice") or []),
        ]
        if self.hashtags:
            lines.append("Hashtags des Unternehmens: " + " ".join(self.hashtags))
        return "\n".join(lines)


def _norm_price(p: str) -> str:
    """ "299€" / "€299" / "299 Euro" → "299 €" (how the scenes show it)."""
    num = re.sub(r"[^\d.,]", "", p).strip(".,")
    return f"{num} €"


def _read_yaml(name: str) -> dict[str, Any]:
    data = yaml.safe_load((SOCIAL_CONFIG / name).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config/social/{name} muss eine Zuordnung enthalten")
    return data


def load_brand() -> Brand:
    """The PJE Systems fact file alone (seed of the first profile; tests)."""
    return Brand(_read_yaml("brand.yaml"))


def brand_for(profile: dict[str, Any], hint: str = "") -> Brand:
    """Profile fields over its fact file: the profile is what Sir edits in the app, the file holds the sourced facts."""
    raw: dict[str, Any] = {}
    file = profile.get("brand_file")
    if file and re.fullmatch(r"[\w.-]+\.ya?ml", str(file)) and (SOCIAL_CONFIG / str(file)).is_file():
        raw = _read_yaml(str(file))
    raw["company_id"] = profile.get("id", "")
    raw["company"] = profile.get("name") or raw.get("company") or ""
    raw["description"] = profile.get("description") or ""
    raw["audience"] = profile.get("audience") or ""
    if profile.get("website"):
        raw["website"] = profile["website"]
    raw.setdefault("website", "")
    if profile.get("cta_button"):
        raw["cta"] = {**(raw.get("cta") or {}), "default_button": profile["cta_button"]}
    raw["colors"] = {**(raw.get("colors") or {}), **(profile.get("colors") or {})}
    raw["logo"] = profile.get("logo") or {}
    raw["hashtags"] = list(profile.get("hashtags") or [])
    if profile.get("tone"):
        raw["voice"] = [profile["tone"]]
    offer, price = str(profile.get("offer") or "").strip(), str(profile.get("price") or "").strip()
    offers = list(raw.get("offers") or [])
    if offer and not any(str(o.get("text", "")).strip() == offer for o in offers):
        offers.insert(0, {"id": "profile_offer", "text": offer, "active": True, "valid_until": None})
    raw["offers"] = offers
    facts = list(raw.get("facts") or [])
    if price and not any(amount(str(f.get("value", ""))) == amount(price) for f in facts if f.get("kind") == "price"):
        facts.insert(0, {"id": "offer_price", "value": price, "label": offer or "Standardpreis", "kind": "price"})
    raw["facts"] = facts
    return Brand(raw, (hint or "").strip())


def load_inspiration() -> dict[str, Any]:
    data = yaml.safe_load((SOCIAL_CONFIG / "inspiration.yaml").read_text(encoding="utf-8")) or {}
    formats = [f for f in data.get("formats") or [] if isinstance(f, dict) and f.get("id")]
    return {"categories": data.get("categories") or {}, "formats": formats}
