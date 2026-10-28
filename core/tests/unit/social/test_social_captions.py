"""Captions: platform limits, hashtag counts, CTA and no invented prices – validated in code."""

from __future__ import annotations

from jarvis.social import captions as cap

from .conftest import captions_ok

PRICES = {"499 €", "799 €", "1.499 €"}


def test_good_captions_pass() -> None:
    check = cap.validate(cap.clean(captions_ok()), PRICES)
    assert check.ok, check.problems


def test_instagram_hashtag_limit_and_count() -> None:
    c = captions_ok()
    c["instagram"]["hashtags"] = [f"#tag{i}" for i in range(6)]
    assert any("instagram: 6 Hashtags" in p for p in cap.validate(c, PRICES).problems)
    c["instagram"]["hashtags"] = ["#eins"]
    assert any("instagram: 1 Hashtags" in p for p in cap.validate(c, PRICES).problems)


def test_character_limits() -> None:
    c = captions_ok()
    c["tiktok"]["text"] = "Projekt anfragen " + "x" * 2200
    assert any(p.startswith("tiktok:") and "max. 2200" in p for p in cap.validate(c, PRICES).problems)
    c = captions_ok()
    c["youtube"]["title"] = "T" * 101
    assert any("Titel 101" in p for p in cap.validate(c, PRICES).problems)
    c = captions_ok()
    c["youtube"]["description"] = "Projekt anfragen <script>"
    assert any("< und >" in p for p in cap.validate(c, PRICES).problems)


def test_utf16_length_for_emojis() -> None:
    assert cap.utf16_len("😀") == 2 and cap.utf16_len("ä") == 1


def test_prices_only_from_configuration() -> None:
    c = captions_ok()
    c["facebook"]["text"] = "Jetzt nur 299 € statt 499 €! Projekt anfragen auf pje-systems.de"
    problems = cap.validate(c, PRICES).problems
    assert any("299 €" in p for p in problems) and not any("„499 €“" in p for p in problems)


def test_cta_and_unbacked_promises() -> None:
    c = captions_ok()
    c["instagram"]["text"] = "Schöne Websites."
    assert any("CTA" in p for p in cap.validate(c, PRICES).problems)
    c = captions_ok()
    c["tiktok"]["text"] = "Garantiert Platz 1 bei Google! Projekt anfragen"
    assert any("garantiert" in p for p in cap.validate(c, PRICES).problems)


def test_clean_normalizes_hashtags() -> None:
    c = cap.clean({"instagram": {"text": " x ", "hashtags": ["Webdesign", "#webdesign", "#Wolnzach!", "# "]}})
    assert c["instagram"]["hashtags"] == ["#Webdesign", "#Wolnzach"] and c["instagram"]["text"] == "x"
    assert cap.full_text("instagram", c["instagram"]) == "x\n\n#Webdesign #Wolnzach"
