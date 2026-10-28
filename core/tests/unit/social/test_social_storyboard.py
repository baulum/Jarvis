"""Storyboard schema: what the LLM writes must fit the Remotion building blocks before anything renders."""

from __future__ import annotations

import copy

from jarvis.social import storyboard as sb
from jarvis.social.brand import load_brand, load_inspiration

from .conftest import board

FACTS = {"price_from"}
WITH_FACT = [{"id": "price_from", "value": "499 €", "label": "Festpreis für einen Onepager. Einmalig, kein Abo."}]


def test_valid_storyboard_passes() -> None:
    check = sb.validate(board(facts=WITH_FACT), fact_ids=FACTS)
    assert check.ok, check.problems
    assert 8 <= sb.total_seconds(board()) <= 12


def test_unknown_component_and_too_long_text_fail_the_schema() -> None:
    b = board()
    b["scenes"][1]["component"] = "hologram"
    assert not sb.validate(b).ok
    b = board()
    b["scenes"][0]["props"]["title"] = "X" * 80  # error_dialog title ≤ 34 characters
    check = sb.validate(b)
    assert not check.ok and "Schema" in check.problems[0]


def test_props_are_checked_per_component() -> None:
    b = board()
    b["scenes"][2]["props"] = {"lines": [{"text": "falsche Props"}], "style": "stack"}  # split_screen props
    assert not sb.validate(b).ok


def test_total_length_end_card_and_hook_rules() -> None:
    long = board()
    long["scenes"][2]["duration"] = 4.5
    long["scenes"][3]["duration"] = 4.5
    long["scenes"][1]["duration"] = 4.5
    assert any("Gesamtlänge" in p for p in sb.validate(long, fact_ids=FACTS).problems)
    no_end = board()
    no_end["scenes"] = [*no_end["scenes"][:-1], copy.deepcopy(no_end["scenes"][1]) | {"id": "s9", "duration": 2.6}]
    assert any("end_card" in p for p in sb.validate(no_end, fact_ids=FACTS).problems)
    slow = board()
    slow["scenes"][0]["component"] = "kinetic_type"
    slow["scenes"][0]["props"] = {"lines": [{"text": "Langsam."}], "style": "stack"}
    slow["scenes"][0]["duration"] = 3.5
    assert any("Hook" in p for p in sb.validate(slow, fact_ids=FACTS).problems)


def test_reading_time_per_scene() -> None:
    b = board(facts=WITH_FACT)
    b["scenes"][0]["props"]["message"] = "Kundenvertrauen wurde leider an keiner bekannten Stelle gefunden."
    b["scenes"][0]["duration"] = 1.2
    assert any("zu kurz zum Lesen" in p for p in sb.validate(b, fact_ids=FACTS).problems)
    # a fact scene counts the value and label it shows
    b = board(facts=WITH_FACT)
    b["scenes"][3]["duration"] = 1.0
    assert any("s4" in p and "zu kurz" in p for p in sb.validate(b, fact_ids=FACTS).problems)


def test_facts_only_from_brand_file_and_satire_flag() -> None:
    b = board()
    b["scenes"][3]["props"]["fact_id"] = "kunden_zufrieden_99"
    assert any("fact_id" in p for p in sb.validate(b, fact_ids=FACTS).problems)
    b = board()
    b["scenes"][1] = {"id": "s2", "component": "review", "duration": 2.4, "transition": "fade",
                      "props": {"stars": 1, "author": "Max M.", "text": "Lädt länger als mein Kaffee."}}  # fmt: skip
    assert any("satire" in p for p in sb.validate(b, fact_ids=FACTS).problems)
    assert sb.normalize(b, title="t", category="A", tone="humorvoll")["satire"] is True


def test_banned_opener_in_first_scene() -> None:
    b = board()
    b["scenes"][0] = {"id": "s1", "component": "kinetic_type", "duration": 2.0, "transition": "cut",
                      "props": {"lines": [{"text": "Deine Website"}, {"text": "ist veraltet?"}], "style": "slam"}}  # fmt: skip
    assert any("Abgedroschener Einstieg" in p for p in sb.validate(b, fact_ids=FACTS).problems)


def test_normalize_rounds_to_frames_and_fills_defaults() -> None:
    raw = {"title": "T", "palette": "ink", "scenes": [{"id": "Szene 1!", "component": "end_card", "duration": 2.017, "props": {}}]}
    out = sb.normalize(raw, title="T", category="C", tone="cineastisch")
    assert out["version"] == 1 and out["category"] == "C" and out["music"] == "pulse"
    assert out["scenes"][0]["id"] == "szene1" and round(out["scenes"][0]["duration"] * 30) == round(2.017 * 30)


def test_summary_and_catalog() -> None:
    s = sb.summary(board())
    assert s["components"][0] == "error_dialog" and s["transitions"] == ["glitch", "wipe", "zoom", "iris"]
    assert "music:pulse" in s["sounds"] and "error_buzz" in s["sounds"]
    cat = sb.component_catalog()
    assert len(sb.COMPONENTS) >= 20 and "end_card" in cat and "Text≤" in cat


def test_brand_and_inspiration_files() -> None:
    brand = load_brand()
    assert brand.website == "pje-systems.de" and "price_from" in brand.fact_ids
    assert brand.allowed_references() == []  # no customer has approved being shown in ads
    insp = load_inspiration()
    assert len(insp["formats"]) == 60
    assert {f["category"] for f in insp["formats"]} == set("ABCDEF")
    assert all(c in sb.COMPONENTS for f in insp["formats"] for c in f["components"])
