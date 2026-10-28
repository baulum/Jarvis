"""Brand facts, offers and the inspiration formats from ``config/social/`` (validated, reloaded on every run so
Sir's edits apply to the next video without a restart)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from jarvis.config import CONFIG_DIR

SOCIAL_CONFIG = CONFIG_DIR / "social"


@dataclass(slots=True)
class Brand:
    raw: dict[str, Any]

    @property
    def company(self) -> str:
        return str(self.raw.get("company", "PJE Systems"))

    @property
    def website(self) -> str:
        return str(self.raw.get("website", "pje-systems.de"))

    @property
    def cta_button(self) -> str:
        return str((self.raw.get("cta") or {}).get("default_button", "Projekt anfragen"))

    @property
    def facts(self) -> list[dict[str, str]]:
        return [
            {"id": str(f["id"]), "value": str(f["value"]), "label": str(f["label"])}
            for f in self.raw.get("facts") or []
            if f.get("id") and f.get("value") and f.get("label")
        ]

    @property
    def fact_ids(self) -> set[str]:
        return {f["id"] for f in self.facts}

    def active_offers(self, today: date | None = None) -> list[str]:
        today = today or date.today()
        out = []
        for o in self.raw.get("offers") or []:
            if not o.get("active"):
                continue
            until = o.get("valid_until")
            if until and date.fromisoformat(str(until)) < today:
                continue
            out.append(str(o["text"]))
        return out

    def allowed_references(self) -> list[dict[str, str]]:
        return [{"name": r["name"], "url": r["url"]} for r in self.raw.get("references") or [] if r.get("allowed")]

    def prompt_block(self, today: date | None = None) -> str:
        r = self.raw
        refs = self.allowed_references()
        return "\n".join(
            [
                f"Firma: {self.company} ({r.get('owner', '')}), {r.get('location', '')}; "
                f"Einsatzgebiet: {r.get('region', '')}",
                f"Website/CTA: {self.cta_button} → {self.website}",
                "Leistungen: " + "; ".join(r.get("services") or []),
                "Belegte Claims (wörtlich von der Website): " + " | ".join(r.get("claims") or []),
                "Fakten für die Szene `fact` (nur diese IDs): "
                + "; ".join(f"{f['id']} = {f['value']} ({f['label']})" for f in self.facts),
                "Gültige Angebote (nur diese Preise/Angebote nennen): "
                + ("; ".join(self.active_offers(today)) or "keine"),
                "Echte Kundenprojekte, die gezeigt werden dürfen: "
                + (
                    ", ".join(f"{x['name']} ({x['url']})" for x in refs)
                    or "keine – keine echten Kunden zeigen oder nennen"
                ),
                "Tonalität: " + " ".join(r.get("voice") or []),
            ]
        )


def load_brand() -> Brand:
    data = yaml.safe_load((SOCIAL_CONFIG / "brand.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config/social/brand.yaml muss eine Zuordnung enthalten")
    return Brand(data)


def load_inspiration() -> dict[str, Any]:
    data = yaml.safe_load((SOCIAL_CONFIG / "inspiration.yaml").read_text(encoding="utf-8")) or {}
    formats = [f for f in data.get("formats") or [] if isinstance(f, dict) and f.get("id")]
    return {"categories": data.get("categories") or {}, "formats": formats}
