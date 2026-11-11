"""Modellnamen im Chat: Schreibweisen, Versionen, Rückfrage statt Raten, keine falschen Treffer."""

from __future__ import annotations

import pytest

from jarvis.agent.model_names import find_mentions, version_of
from jarvis.agent.model_router import describe, model_hint, model_id, parse_command, resolve_alias, unclear_model_note

CANON = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}


@pytest.mark.parametrize(
    "spelling",
    [
        "haiku", "Haiku", "HAIKU", "h.a.i.k.u", "H.A.I.K.U", "h a i k u", "h-a-i-k-u", "h_a_i_k_u", "h. a. i. k. u.",
    ],
)  # fmt: skip
@pytest.mark.parametrize(
    "version", ["", " 5.5", " 5,5", " 5-5", " fünf komma fünf", " fünf punkt fünf", " Fünf Punkt Fünf", " 5 komma 5"]
)
def test_haiku_spellings_and_versions_map_to_canonical_id(spelling: str, version: str) -> None:
    c = parse_command(f"Nimm {spelling}{version}")
    assert c is not None and c.clarify is None and c.model == "haiku", (spelling, version)
    assert c.remainder == "" and c.scope == "session"
    assert model_id(c.model) == CANON["haiku"]


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("nimm sonnet 5.5", "sonnet"),
        ("Wechsle auf S.o.n.n.e.t 5,5", "sonnet"),
        ("benutze o.p.u.s fünf komma fünf", "opus"),
        ("nutze o p u s", "opus"),
        ("stell auf O-P-U-S 5.5 um", "opus"),
        ("Haiku 5.5 nutzen", "haiku"),
        ("h.a.i.k.u fünf punkt fünf verwenden", "haiku"),
        ("Opus 5.5 bitte nehmen", "opus"),
        ("nimm 5.5 Opus", "opus"),
    ],
)
def test_other_phrasings(text: str, family: str) -> None:
    c = parse_command(text)
    assert c is not None and c.clarify is None and c.model == family, text
    assert model_id(c.model) == CANON[family]


def test_scope_and_remainder_survive_the_version() -> None:
    c = parse_command("Nimm nur diesmal h.a.i.k.u 5.5 und fasse mir den Termin von morgen zusammen")
    assert c and c.model == "haiku" and c.scope == "turn"
    assert c.remainder.startswith("fasse mir den Termin")
    assert "5.5" not in c.remainder and "h.a" not in c.remainder
    c = parse_command("Wechsle ab jetzt auf Opus 5,5")
    assert c and c.model == "opus" and c.scope == "default" and c.remainder == ""


def test_bare_version_asks_instead_of_choosing() -> None:
    for text in ("nimm 5.5", "Nimm bitte 5,5", "Modell fünf punkt fünf", "stell auf Version 5.5 um"):
        c = parse_command(text)
        assert c is not None and c.clarify, text
        assert c.model is None and "Haiku, Sonnet oder Opus" in c.clarify and "5.5" in c.clarify
    assert parse_command("Das kostet 5.5 Euro") is None
    assert parse_command("5.5") is None  # no switching context at all: nothing happens


def test_unknown_version_asks_instead_of_rounding_to_5_5() -> None:
    c = parse_command("Nimm Haiku 4.5")
    assert c is not None and c.model is None and c.clarify
    assert "4.5" in c.clarify and "5.5" in c.clarify and "nichts um" in c.clarify
    c = parse_command("nimm h.a.i.k.u vier komma fünf")
    assert c is not None and c.clarify and "4.5" in c.clarify


def test_unknown_words_choose_nothing() -> None:
    for text in ("nimm Gemini 5.5", "wechsle auf GPT", "nimm h.a.i.l.u", "nimm Haikuu", "nimm Opuss"):
        c = parse_command(text)
        assert c is None or (c.model is None and c.clarify), text  # at most a question, never a model
    assert parse_command("nimm Gemini") is None


@pytest.mark.parametrize(
    "text",
    [
        "Erzähl mir etwas über Haiku-Gedichte",
        "Schreibe ein Haiku über den Herbst",
        "Ein Haiku hat 17 Silben, verteilt auf 5-7-5",
        "Was ist der Unterschied zwischen Opus und Sonnet?",
        "Lies mir ein Sonett von Shakespeare vor",
        "Wie war Opus 5 Dei gemeint?",
        "Ich habe 5 Haiku-Gedichte geschrieben",
        "Die Version 5.5 kostet 20 Euro",
    ],
)
def test_ordinary_sentences_do_not_change_the_model(text: str) -> None:
    c = parse_command(text)
    assert c is None, text
    assert model_hint(text) == "" or "Haiku-Gedichte" not in text


def test_coding_job_context_never_switches_the_chat_model() -> None:
    for text in (
        "Starte den Coding-Job mit Opus 5.5",
        "nimm für den Job h.a.i.k.u 5.5",
        "Der Auftrag soll mit Sonnet laufen, nimm Sonnet",
    ):
        assert parse_command(text) is None, text


def test_version_parsing() -> None:
    assert version_of("5.5") == version_of("5,5") == version_of("fünf komma fünf") == "5.5"
    assert version_of("fünf punkt fünf") == version_of("5 punkt 5") == version_of("5-5") == "5.5"
    assert version_of("4.5") == "4.5" and version_of("5") == "5"


def test_find_mentions_reports_spelling_and_version() -> None:
    (m,) = find_mentions("bitte h.a.i.k.u 5,5 nutzen")
    assert (m.family, m.version, m.spelled_out, m.version_known) == ("haiku", "5.5", True, True)
    (m,) = find_mentions("Opus 4.5")
    assert m.version == "4.5" and not m.version_known and not m.spelled_out
    (m,) = find_mentions("Sonnet")
    assert m.version is None and m.version_known
    assert find_mentions("nur 5.5") == []


def test_resolve_alias_accepts_spelled_names_but_never_guesses() -> None:
    assert resolve_alias("h.a.i.k.u 5,5") == "haiku"
    assert resolve_alias("Opus fünf komma fünf") == "opus"
    assert resolve_alias("sonnet") == "sonnet" and resolve_alias("claude-opus-5-5") == "opus"
    assert resolve_alias("5.5") is None
    assert resolve_alias("haiku 4.5") is None
    assert resolve_alias("haiku und opus") is None
    assert resolve_alias("gemini") is None and resolve_alias("auto") is None
    assert "nachfragen" in unclear_model_note("5.5") and "4.5" in unclear_model_note("haiku 4.5")


def test_describe_and_hint() -> None:
    assert describe("haiku") == "Haiku 5.5 (claude-haiku-5-5)"
    hint = model_hint("Starte einen Coding-Job mit h.a.i.k.u 5.5")
    assert "claude-haiku-5-5" in hint and "agent_set_model" in hint and "rate nicht" in hint
    assert "unbekannte Version" in model_hint("Starte einen Job mit Opus 4.5")
    assert "ohne Modellfamilie" in model_hint("nimm 5.5")
    assert model_hint("Wie spät ist es?") == ""
    assert model_hint("Schreibe ein Haiku") == ""
