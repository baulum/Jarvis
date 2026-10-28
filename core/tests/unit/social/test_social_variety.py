"""Anti-repetition rules: enforced in code, independent of what the LLM promises."""

from __future__ import annotations

from datetime import date, timedelta

from jarvis.social.variety import Candidate, Past, constraints, opener, violations

TODAY = date(2026, 10, 28)


def past(days_ago: int, **kw: object) -> Past:
    base = {
        "category": "A", "concept_key": f"konzept-{days_ago}", "hook": f"Hook Nummer {days_ago}", "tone": "humorvoll",
        "palette": "ink", "music": "pulse", "components": ["chat", "kinetic_type", "end_card"],
        "transitions": ["fade", "wipe"], "title": f"Video {days_ago}",
    }  # fmt: skip
    base.update(kw)
    return Past(day=TODAY - timedelta(days=days_ago), **base)  # type: ignore[arg-type]


def cand(**kw: object) -> Candidate:
    base = {
        "category": "C", "concept_key": "neues-konzept", "hook": "Was kostet ein Klick?", "tone": "cineastisch",
        "palette": "canvas", "music": "glow", "components": ["error_dialog", "split_screen", "end_card"],
        "transitions": ["glitch", "iris"],
    }  # fmt: skip
    base.update(kw)
    return Candidate(**base)  # type: ignore[arg-type]


def test_fresh_candidate_passes() -> None:
    assert violations(cand(), [past(1), past(2, category="B")], TODAY) == []


def test_same_category_as_yesterday_is_rejected() -> None:
    v = violations(cand(category="A"), [past(1, category="A")], TODAY)
    assert any("Kategorie A" in x for x in v)


def test_same_category_two_days_ago_is_fine() -> None:
    v = violations(cand(category="A"), [past(1, category="B"), past(2, category="A")], TODAY)
    assert not any("Kategorie" in x for x in v)


def test_concept_blocked_for_30_days() -> None:
    hist = [past(1), past(29, concept_key="Fake Fehlermeldung 404")]
    assert any("Grundkonzept" in x for x in violations(cand(concept_key="fake-fehlermeldung-404"), hist, TODAY))
    old = [past(1), past(30, concept_key="fake-fehlermeldung-404")]
    assert not any("Grundkonzept" in x for x in violations(cand(concept_key="fake-fehlermeldung-404"), old, TODAY))


def test_hook_opener_and_banned_template_opener() -> None:
    hist = [past(1, hook="Deine Konkurrenz schläft nicht.")]
    assert any("Hook-Einstieg" in x for x in violations(cand(hook="Deine Konkurrenz schläft nie"), hist, TODAY))
    assert any("verbotenen Floskel" in x for x in violations(cand(hook="Deine Website ist veraltet? Dann…"), [], TODAY))
    assert opener("Deine Website, ist…") == "deine website ist"


def test_tone_palette_music_opening_and_components_vary() -> None:
    last = past(1, tone="cineastisch", palette="canvas", music="glow", components=["error_dialog", "split_screen", "end_card"],
                transitions=["iris", "glitch"])  # fmt: skip
    v = violations(cand(), [last], TODAY)
    assert any("Tonalität" in x for x in v)
    assert any("Farbvariante" in x for x in v)
    assert any("Musik-Bett" in x for x in v)
    assert any("Einstieg mit error_dialog" in x for x in v)
    assert any("Bausteine" in x for x in v)
    assert any("Übergänge" in x for x in v)


def test_constraints_brief_for_the_llm() -> None:
    hist = [past(1, category="A", tone="provokant"), past(2, category="C"), past(3, category="C")]
    c = constraints(hist, TODAY)
    assert c["forbidden_categories"] == ["A"]
    assert "A" not in c["preferred_categories"]
    # least used categories of the month come first (B, D, E, F never used; C twice)
    assert c["preferred_categories"][:4] == ["B", "D", "E", "F"] and c["preferred_categories"][-1] == "C"
    assert c["forbidden_tone"] == "provokant" and "provokant" not in c["preferred_tones"]
    assert "deine website ist veraltet" in c["banned_openers"]
    assert c["avoid_opening_components"] == ["chat"]


def test_performance_feeds_the_brief() -> None:
    hist = [past(1, metrics={"instagram": {"reach": 1200, "website_clicks": 7}})]
    assert "reach=1200" in constraints(hist, TODAY)["performance"][0]
