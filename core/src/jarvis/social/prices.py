"""Prices in free text ("299 €", "1.499 Euro", "€ 49") – found, compared by amount, never by spelling.

Shared by the brand brief (which prices Sir's input sets), the storyboard check, the captions check and the QA."""

from __future__ import annotations

import re

PRICE_RE = re.compile(r"\d[\d.,]*\s?(?:€|euro\b|eur\b)|€\s?\d[\d.,]*", re.IGNORECASE)


def amount(price: str) -> str:
    """ "1.499 €" → "1499", "499,00 €" → "499": compare amounts, not spellings."""
    num = re.sub(r"[^\d.,]", "", price)
    num = re.sub(r"[.,]00$", "", num)
    return re.sub(r"\D", "", num)


def prices_in(text: str) -> set[str]:
    return {p.strip() for p in PRICE_RE.findall(text or "")}


def amounts_in(text: str) -> set[str]:
    return {a for a in (amount(p) for p in prices_in(text)) if a}
