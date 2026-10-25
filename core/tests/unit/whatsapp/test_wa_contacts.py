from __future__ import annotations

from jarvis.whatsapp.contacts import normalize, resolve

ENTRIES = [
    {"jid": "4917000000001@s.whatsapp.net", "names": ["Jonathan Weber", "Jo"], "last_ts": 10},
    {"jid": "4917000000002@s.whatsapp.net", "names": ["Jonathan Müller"], "last_ts": 5},
    {"jid": "4917000000003@s.whatsapp.net", "names": ["Anna Schmidt"], "last_ts": 1},
    {"jid": "120363000000000001@g.us", "names": ["Projekt Müller GmbH"], "is_group": True},
]


def test_normalise_umlauts_and_case() -> None:
    assert normalize("MÜLLER, Jonathan!") == normalize("mueller jonathan")


def test_unique_name_resolves() -> None:
    res = resolve("Anna", ENTRIES)
    assert res.match is not None and res.match.jid == "4917000000003@s.whatsapp.net"
    res = resolve("jonathan weber", ENTRIES)
    assert res.match is not None and res.match.jid.endswith("01@s.whatsapp.net")
    res = resolve("Jonathan Mueller", ENTRIES)
    assert res.match is not None and res.match.jid.endswith("02@s.whatsapp.net")


def test_ambiguous_first_name_returns_candidates_instead_of_guessing() -> None:
    res = resolve("Jonathan", ENTRIES)
    assert res.match is None
    assert {c.jid for c in res.candidates[:2]} == {"4917000000001@s.whatsapp.net", "4917000000002@s.whatsapp.net"}


def test_typos_and_groups() -> None:
    assert resolve("Ana Schmit", ENTRIES).match is None or resolve("Ana Schmit", ENTRIES).match.jid.endswith("03@s.whatsapp.net")  # type: ignore[union-attr]
    res = resolve("Projekt Müller", ENTRIES)
    assert res.match is not None and res.match.is_group


def test_numbers_and_ids() -> None:
    assert resolve("+49 170 00000003", ENTRIES).match.jid.endswith("03@s.whatsapp.net")  # type: ignore[union-attr]
    unknown = resolve("+49 151 99999999", ENTRIES)
    assert unknown.match is None and unknown.unknown_number == "4915199999999"
    assert resolve("120363000000000001@g.us", ENTRIES).match.is_group  # type: ignore[union-attr]
    assert resolve("nobody", ENTRIES).match is None
