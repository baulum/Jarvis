"""Company profiles: one brand per profile – positioning, audience, default offer and price, colours, tone, hashtags,
target accounts per platform, the recipient list of finished videos and autopost.

Stored in ``<data_dir>/social-companies.json`` (Sir edits them in the app). The first load migrates the former
single-brand setup: PJE Systems becomes the first profile with exactly the values the pipeline used before
(``config/social/brand.yaml`` stays its fact base, recipients = Sir + ``social_media.video_recipients``, autopost
as it was switched); "Apo HR" is added as an empty example profile Sir fills in himself (daily run off).

A profile's defaults are only defaults: what Sir writes into the "Produzieren" field wins (``brand.Brand.with_hint``).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.social.variety import slug

logger = logging.getLogger(__name__)

DEFAULT_ID = "pje-systems"
PLATFORMS = ("instagram", "facebook", "tiktok", "youtube")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
TAG_RE = re.compile(r"^#?[0-9A-Za-zÄÖÜäöüß_]{2,40}$")
COLOR_KEYS = ("accent", "ink", "canvas", "accent_deep", "accent_on_dark", "graphite")
DEFAULT_COLORS = {"accent": "#2651f0", "ink": "#0b0c0e", "canvas": "#e3e5e8"}
# fields Sir must fill in before a video can be produced (the end card needs a button and an address)
REQUIRED = {
    "name": "Name",
    "description": "Beschreibung/Positionierung",
    "website": "Website",
    "cta_button": "CTA-Button",
}

TEXT_LIMITS = {
    "name": 60,
    "description": 400,
    "audience": 300,
    "website": 60,
    "cta_button": 22,
    "offer": 200,
    "price": 40,
    "tone": 400,
    "hint": 500,
}


class CompanyError(ValueError):
    """Invalid profile data or an operation that is not allowed (shown to Sir as is)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _list(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\n;]", v)
    return [str(x).strip() for x in v or [] if str(x).strip()]


def blank(name: str = "", **over: Any) -> dict[str, Any]:
    return {
        "id": "",
        "name": name,
        "description": "",
        "audience": "",
        "website": "",
        "cta_button": "",
        "offer": "",
        "price": "",
        "colors": dict(DEFAULT_COLORS),
        "logo": {},
        "tone": "",
        "hashtags": [],
        "accounts": {p: "" for p in PLATFORMS},
        "uptik_accounts": [],
        "recipients": [],
        "autopost": False,
        "official_api": False,
        "hint": "",
        "brand_file": None,
        "created_at": _now(),
        "updated_at": _now(),
        **over,
    }


def clean(data: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validated profile: ``base`` (existing profile or blank) updated with the known keys of ``data``."""
    out: dict[str, Any] = json.loads(json.dumps(base or blank()))
    for key, limit in TEXT_LIMITS.items():
        if key in data:
            out[key] = _text(data[key], limit)
    if "website" in data:
        out["website"] = re.sub(r"^https?://", "", out["website"]).rstrip("/")
    if "colors" in data and isinstance(data["colors"], dict):
        for k in COLOR_KEYS:
            if k not in data["colors"]:
                continue
            val = str(data["colors"][k] or "").strip()
            if not val:
                if k in DEFAULT_COLORS:
                    out["colors"][k] = DEFAULT_COLORS[k]
                else:
                    out["colors"].pop(k, None)
                continue
            if not HEX_RE.match(val):
                raise CompanyError(f"Farbe {k}: {val!r} ist kein Hex-Wert wie #2651f0")
            out["colors"][k] = val.lower()
    if "hashtags" in data:
        tags: list[str] = []
        for t in _list(data["hashtags"]):
            if not TAG_RE.match(t):
                raise CompanyError(f"Ungültiger Hashtag {t!r}")
            t = "#" + t.lstrip("#")
            if t.lower() not in (x.lower() for x in tags):
                tags.append(t)
        out["hashtags"] = tags[:12]
    if "accounts" in data and isinstance(data["accounts"], dict):
        for p in PLATFORMS:
            if p in data["accounts"]:
                out["accounts"][p] = _text(data["accounts"][p], 80)
    if "uptik_accounts" in data:
        out["uptik_accounts"] = list(dict.fromkeys(_text(a, 80) for a in _list(data["uptik_accounts"])))[:24]
    if "recipients" in data:
        rec: list[str] = []
        for r in _list(data["recipients"]):
            if not EMAIL_RE.match(r):
                raise CompanyError(f"Ungültige E-Mail-Adresse {r!r}")
            if r.lower() not in (x.lower() for x in rec):
                rec.append(r)
        out["recipients"] = rec[:20]
    for key in ("autopost", "official_api"):
        if key in data:
            out[key] = bool(data[key])
    if not out["name"]:
        raise CompanyError("Name fehlt")
    out["updated_at"] = _now()
    return out


def missing(profile: dict[str, Any]) -> list[str]:
    """Labels of the fields a production needs and the profile does not have yet."""
    return [label for key, label in REQUIRED.items() if not str(profile.get(key) or "").strip()]


def public(profile: dict[str, Any]) -> dict[str, Any]:
    return {**profile, "missing": missing(profile)}


def pje_seed(brand_raw: dict[str, Any], settings: dict[str, Any], recipients: list[str]) -> dict[str, Any]:
    """PJE Systems exactly as the single-brand pipeline ran it (facts, claims, references stay in brand.yaml)."""
    colors = {k: str(v).lower() for k, v in (brand_raw.get("colors") or {}).items() if k in COLOR_KEYS and v}
    offers = [o for o in brand_raw.get("offers") or [] if o.get("active")]
    price = next((f for f in brand_raw.get("facts") or [] if f.get("id") == "price_from"), None)
    return blank(
        brand_raw.get("company") or "PJE Systems",
        id=DEFAULT_ID,
        description="eine kleine Webdesign-Agentur aus Wolnzach",
        audience=f"Unternehmen in {brand_raw.get('region') or 'München, Wolnzach und Umgebung'}",
        website=brand_raw.get("website") or "pje-systems.de",
        cta_button=(brand_raw.get("cta") or {}).get("default_button") or "Projekt anfragen",
        offer=str(offers[0]["text"]) if offers else "",
        price=str(price["value"]) if price else "",
        colors={**DEFAULT_COLORS, **colors},
        logo={"on_dark": "brand/pje-logo-white.png", "on_light": "brand/pje-logo-ink.png"},
        tone=" ".join(brand_raw.get("voice") or []),
        hashtags=["#Webdesign", "#Wolnzach", "#Handwerk"],
        recipients=recipients,
        autopost=bool(settings.get("autopost_enabled")),
        official_api=True,
        hint=str(settings.get("hint") or ""),
        brand_file="brand.yaml",
    )


def apo_hr_example(recipients: list[str]) -> dict[str, Any]:
    """Example profile – only what Sir told Jarvis; he fills in the rest (website, offer, price, colours …)."""
    return blank(
        "Apo HR",
        id="apo-hr",
        description="HR-Software als Multi-Tenant-SaaS",
        recipients=recipients,
    )


class CompanyStore:
    """Profiles plus the active one (default for tools and the app when no company is named)."""

    def __init__(self, path: Path, seed: Any) -> None:
        self.path = path
        self._seed = seed  # () -> list of profiles for the first start
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("companies"):
                    return data
            except (OSError, json.JSONDecodeError):
                logger.warning("social-companies.json unreadable – profiles migrated again from the defaults")
        companies = self._seed()
        data = {"active": companies[0]["id"], "companies": companies}
        self._write(data)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = self._read()
        ids = [c["id"] for c in data["companies"]]
        if data.get("active") not in ids:
            data["active"] = DEFAULT_ID if DEFAULT_ID in ids else ids[0]
        return data

    def all(self) -> list[dict[str, Any]]:
        companies: list[dict[str, Any]] = self.load()["companies"]
        return companies

    def active_id(self) -> str:
        return str(self.load()["active"])

    def get(self, cid: str) -> dict[str, Any] | None:
        return next((c for c in self.all() if c["id"] == cid), None)

    def resolve(self, ref: str | None = None) -> dict[str, Any]:
        """By id or name (case-insensitive); empty = the active profile."""
        data = self.load()
        ref = str(ref or "").strip()
        if not ref:
            ref = data["active"]
        key = ref.lower()
        companies: list[dict[str, Any]] = data["companies"]
        for c in companies:
            if c["id"] == ref or c["name"].lower() == key or c["id"] == slug(ref, 40):
                return c
        names = ", ".join(c["name"] for c in data["companies"])
        raise CompanyError(f"Unternehmen „{ref}“ gibt es nicht – vorhanden: {names}")

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            store = self._read()
            profile = clean(data, blank(created_at=_now()))
            base = slug(profile["name"], 40) or "unternehmen"
            ids = {c["id"] for c in store["companies"]}
            if any(c["name"].lower() == profile["name"].lower() for c in store["companies"]):
                raise CompanyError(f"Ein Unternehmen „{profile['name']}“ gibt es schon")
            cid, n = base, 2
            while cid in ids:
                cid, n = f"{base}-{n}", n + 1
            profile["id"] = cid
            store["companies"].append(profile)
            self._write(store)
            return profile

    def update(self, cid: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            store = self._read()
            idx = next((i for i, c in enumerate(store["companies"]) if c["id"] == cid), None)
            if idx is None:
                raise CompanyError("Unternehmen nicht gefunden")
            profile = clean(data, store["companies"][idx])
            if any(c["name"].lower() == profile["name"].lower() and c["id"] != cid for c in store["companies"]):
                raise CompanyError(f"Ein Unternehmen „{profile['name']}“ gibt es schon")
            store["companies"][idx] = profile
            self._write(store)
            return profile

    def delete(self, cid: str) -> dict[str, Any]:
        with self._lock:
            store = self._read()
            gone: dict[str, Any] | None = next((c for c in store["companies"] if c["id"] == cid), None)
            if gone is None:
                raise CompanyError("Unternehmen nicht gefunden")
            if len(store["companies"]) == 1:
                raise CompanyError("Das letzte Unternehmen kann nicht gelöscht werden")
            store["companies"] = [c for c in store["companies"] if c["id"] != cid]
            if store.get("active") == cid:
                store["active"] = store["companies"][0]["id"]
            self._write(store)
            return gone

    def set_active(self, cid: str) -> str:
        with self._lock:
            store = self._read()
            if not any(c["id"] == cid for c in store["companies"]):
                raise CompanyError("Unternehmen nicht gefunden")
            store["active"] = cid
            self._write(store)
            return cid
