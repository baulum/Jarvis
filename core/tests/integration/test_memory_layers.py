"""Memory engine v2: entity resolution, dedupe, hybrid retrieval, consolidation, episodes, graph sync, events."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from jarvis.config import get_settings
from jarvis.db import session_scope
from jarvis.db.models import ChatMessage, ChatSession
from jarvis.events import activity, bus
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault

pytestmark = pytest.mark.integration


class FakeLLM:
    """Deterministic structured LLM: returns queued answers and records prompts."""

    def __init__(self) -> None:
        self.answers: list[Any] = []
        self.prompts: list[str] = []

    async def __call__(self, system: str, prompt: str, schema: dict) -> Any:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else {"facts": []}


@pytest.fixture(scope="module")
def embedder() -> LocalEmbedder:
    s = get_settings()
    return LocalEmbedder(s.paths.data_dir / "models" / "fastembed", s.llm.embedding_model)


@pytest.fixture
async def mem(tmp_path: Path, embedder: LocalEmbedder):
    llm = FakeLLM()
    svc = MemoryService(Vault(tmp_path / "vault"), embedder, llm)
    svc.vault.ensure()
    svc.llm = llm  # type: ignore[attr-defined]
    yield svc


class Collector:
    def __init__(self) -> None:
        self.q = bus.open_queue()

    def drain(self, type_: str | None = None) -> list[Any]:
        out = []
        while not self.q.empty():
            ev = self.q.get_nowait()
            if type_ is None or ev.type == type_:
                out.append(ev)
        return out

    def close(self) -> None:
        bus.close_queue(self.q)


async def test_entity_resolution_merges_prefixed_name(mem: MemoryService) -> None:
    a = await mem.remember("Müller Gebäudereinigung", "Neuer Kunde, braucht eine Website für 499 Euro.", "customer")
    b = await mem.remember(
        "Kunde Müller Gebäudereinigung", "Der Kunde braucht eine Website für einen Betrag von 499 Euro.", "customer"
    )
    assert b["id"] == a["id"] and b["status"] in ("duplicate", "updated")
    note = mem.vault.read(a["path"])
    assert "Kunde Müller Gebäudereinigung" in note.aliases  # the variant is kept as alias
    assert note.body.count("499") == 1  # the paraphrase was not appended
    c = await mem.remember("Müller Gebäudereinigung GmbH", "Ansprechpartner ist Max Müller.", "company")
    assert c["id"] == a["id"] and c["status"] == "updated"
    assert "Max Müller" in mem.vault.read(a["path"]).body
    assert len(list((mem.vault.root / "Kunden").glob("*.md"))) == 1


async def test_project_note_is_not_merged_into_customer(mem: MemoryService) -> None:
    cust = await mem.remember("Westside Taxi UG", "Taxiunternehmen aus Ingolstadt.", "customer")
    proj = await mem.remember("Westside Taxi Website", "Website mit Tarifrechner, Lieferung Freitag.", "project")
    assert proj["status"] == "created" and proj["id"] != cust["id"]


async def test_hybrid_search_keyword_only_term_and_graph_expansion(mem: MemoryService) -> None:
    await mem.remember("Bäckerei Schmidt", "Kunde aus Celle. Ansprechpartnerin Frau Schmidt.", "customer")
    await mem.remember(
        "Projekt Brotshop",
        "Onlineshop für die Bäckerei. Bestellnummer QX-7781 im Lieferantenportal.",
        "project",
        entities=[{"name": "Bäckerei Schmidt", "type": "customer"}],
    )
    hits = await mem.search("QX-7781", limit=3)
    assert hits and hits[0]["title"] == "Projekt Brotshop" and "keyword" in hits[0]["match"]
    hits = await mem.search("Was weißt du über den Brotshop?", limit=5)
    titles = [h["title"] for h in hits]
    assert "Projekt Brotshop" in titles and "Bäckerei Schmidt" in titles  # neighbour via graph link


async def test_recall_event_carries_nodes_and_activity(mem: MemoryService) -> None:
    r = await mem.remember("Präferenz Rechnungsversand", "Rechnungen immer als PDF per E-Mail.", "preference")
    col = Collector()
    try:
        with activity(session_id="sess-1", tool="memory_search"):
            await mem.search("Wie verschicke ich Rechnungen?", limit=2)
        evs = col.drain("memory.recalled")
    finally:
        col.close()
    assert evs, "memory.recalled must be published"
    ev = evs[-1]
    assert f"memory:{r['id']}" in ev.nodes and "session:sess-1" in ev.nodes
    assert ev.session_id == "sess-1" and ev.data["via"] == "memory_search" and ev.source == "memory"
    d = ev.to_dict()
    assert {"id", "seq", "ts", "type", "source", "session_id", "task_id", "nodes", "data"} <= d.keys()


async def test_search_budget_falls_back_to_keywords(mem: MemoryService) -> None:
    await mem.remember("Server Zugang", "Der Staging-Server läuft auf Port 8443.", "technical")
    hits = await mem.search("Staging-Server Port Neuabfrage", limit=2, budget_ms=0.001)
    assert hits and hits[0]["title"] == "Server Zugang"


async def test_context_for_compresses(mem: MemoryService) -> None:
    long = " ".join(f"Satz {i} über etwas anderes." for i in range(40)) + " Das Zahlungsziel beträgt 14 Tage."
    await mem.remember("Konditionen Lexware", long, "process")
    ctx = await mem.context_for("Welches Zahlungsziel gilt bei Lexware?", include_sensitive=True)
    assert "14 Tage" in ctx and len(ctx) < 700


async def test_extraction_update_consolidates_with_candidates(mem: MemoryService) -> None:
    base = await mem.remember("Westside Taxi UG", "Taxiunternehmen aus Ingolstadt.", "customer")
    mem.llm.answers.append(  # type: ignore[attr-defined]
        {
            "facts": [
                {
                    "action": "update",
                    "target_id": base["id"],
                    "title": "Westside Taxi UG",
                    "kind": "customer",
                    "content": "Taxiunternehmen aus Ingolstadt. Geschäftsführer ist Ali Demir.",
                    "entities": [{"name": "Ali Demir", "type": "person"}],
                    "relations": [{"relation": "Geschäftsführer", "target": "Ali Demir", "type": "person"}],
                    "sensitive": False,
                    "confidence": 0.9,
                }
            ]
        }
    )
    facts = await mem.extract(
        "Der Geschäftsführer von Westside Taxi heißt Ali Demir.",
        "Notiert.",
        context=[{"role": "user", "content": "Wir haben einen neuen Kunden, Westside Taxi."}],
    )
    prompt = mem.llm.prompts[-1]  # type: ignore[attr-defined]
    assert base["id"] in prompt and "neuen Kunden, Westside Taxi" in prompt  # candidates + history are shown
    res = await mem.apply_fact(facts[0], {"session_id": "s"})
    assert res["status"] == "updated" and res["id"] == base["id"]
    note = mem.vault.read(base["path"])
    assert "Ali Demir" in note.body and "## Verlauf" in note.body
    assert {"relation": "Geschäftsführer", "target": "Ali Demir", "kind": "person"} in note.relations
    graph = await mem.graph()
    assert any(e["relation"] == "Geschäftsführer" for e in graph["edges"])


async def test_extraction_supersede_on_contradiction(mem: MemoryService) -> None:
    old = await mem.remember("Preis Website Müller", "Die Website kostet 499 Euro.", "project")
    mem.llm.answers.append(  # type: ignore[attr-defined]
        {
            "facts": [
                {
                    "action": "supersede",
                    "target_id": old["id"],
                    "title": "Preis Website Müller",
                    "kind": "project",
                    "content": "Die Website kostet jetzt 599 Euro.",
                    "entities": [],
                    "sensitive": False,
                    "confidence": 0.95,
                }
            ]
        }
    )
    facts = await mem.extract("Der Preis für die Müller-Website ist jetzt 599 Euro.", "Verstanden.")
    res = await mem.apply_fact(facts[0], {})
    assert res["status"] == "superseded"
    hits = await mem.search("Preis Website Müller", limit=3)
    assert hits[0]["content"].startswith("Die Website kostet jetzt 599")
    assert all(h["status"] == "active" for h in hits)
    assert mem.vault.read(old["path"]).status == "superseded"


async def test_low_confidence_and_invalid_target_are_handled(mem: MemoryService) -> None:
    mem.llm.answers.append(  # type: ignore[attr-defined]
        {
            "facts": [
                {"action": "create", "target_id": "", "title": "Vage", "kind": "note", "content": "Vielleicht irgendwas.",
                 "entities": [], "sensitive": False, "confidence": 0.3},
                {"action": "update", "target_id": str(uuid.uuid4()), "title": "Neu", "kind": "note",
                 "content": "Ein ganz neuer Fakt über Glasfaser.", "entities": [], "sensitive": False,
                 "confidence": 0.8},
            ]
        }
    )
    facts = await mem.extract("Bitte merk dir den neuen Fakt über Glasfaser.", "Ok.")
    assert len(facts) == 1 and facts[0]["action"] == "create" and facts[0]["target_id"] == ""


async def test_consolidate_existing_duplicates(mem: MemoryService) -> None:
    from jarvis.memory.vault import Note

    for title, body in (
        ("Kunde Müller Gebäudereinigung", "Der Kunde benötigt eine Website für 499 Euro."),
        ("Müller Gebäudereinigung", "Neuer Kunde von Sir. Ansprechpartner noch offen."),
    ):  # written directly, as the old engine did
        note = Note(title=title, body=body, kind="customer")
        mem.vault.write(note)
        await mem.index_note(note)
    groups = await mem.find_duplicates()
    assert len(groups) == 1 and len(groups[0]) == 2
    res = await mem.consolidate_duplicates()
    assert res and res[0]["merged"] == 1
    active = await mem.list_items(kind="customer", status="active")
    assert active["total"] == 1 and active["items"][0]["title"] == "Müller Gebäudereinigung"
    body = mem.vault.read(active["items"][0]["path"]).body
    assert "499 Euro" in body and "Ansprechpartner" in body
    assert (await mem.list_items(kind="customer", status="superseded"))["total"] == 1  # nothing deleted


async def test_edit_in_place_keeps_alias(mem: MemoryService) -> None:
    r = await mem.remember("Projekt Telefnie", "Twilio-Anbindung.", "project")
    await mem.edit(uuid.UUID(r["id"]), "pytest", title="Projekt Telefonie", content="Twilio-Anbindung mit PIN.")
    item = await mem.get(uuid.UUID(r["id"]))
    assert item and item["title"] == "Projekt Telefonie" and "PIN" in item["content"]
    assert "Projekt Telefnie" in item["aliases"]


async def _session_with_turns(turns: list[tuple[str, str]], extracted: bool = True) -> uuid.UUID:
    async with session_scope() as s:
        sess = ChatSession(channel="desktop", actor_role="owner", actor_id="pytest")
        s.add(sess)
        await s.flush()
        for u, a in turns:
            s.add(ChatMessage(session_id=sess.id, role="user", content=u, meta={} if not extracted else {"extracted": True}))
            await s.flush()
            s.add(ChatMessage(session_id=sess.id, role="assistant", content=a, meta={}))
            await s.flush()
        return sess.id


async def test_episode_summary_written_and_marked(mem: MemoryService) -> None:
    sid = await _session_with_turns(
        [("Leg bitte eine Rechnung für Westside Taxi über 299 Euro an.", "Entwurf angelegt."),
         ("Und trag mir morgen 9 Uhr Arbeit an der Website ein.", "Termin eingetragen.")]
    )
    mem.llm.answers.append(  # type: ignore[attr-defined]
        {"worth_saving": True, "title": "Rechnung Westside Taxi", "summary": "Sir ließ eine Rechnung über 299 Euro anlegen.",
         "entities": [{"name": "Westside Taxi UG", "type": "customer"}], "decisions": ["Rechnung als Entwurf"],
         "open_items": ["Website morgen ab 9 Uhr"]}
    )
    res = await mem.summarize_session(sid)
    assert res and res["status"] == "created"
    note = mem.vault.read(res["path"])
    assert note.kind == "conversation" and note.title.startswith("Gespräch ") and "Offen:" in note.body
    async with session_scope() as s:
        assert (await s.get(ChatSession, sid)).summarized_at is not None


async def test_unextracted_turns_are_recovered(mem: MemoryService) -> None:
    await _session_with_turns([("Merke dir: mein Steuerberater heißt Herr Krause aus Hamburg.", "Notiert.")], extracted=False)
    n = await mem._recover_unextracted()
    assert n == 1 and mem._extract_queue.qsize() == 1
    job = mem._extract_queue.get_nowait()
    assert "Krause" in job["user"] and job["message_id"]
    await mem._mark_extracted(job["message_id"])
    assert await mem._recover_unextracted() == 0


async def test_memory_changed_events_carry_node_ids(mem: MemoryService) -> None:
    col = Collector()
    try:
        r = await mem.remember("Notiz Glasfaser", "Glasfaseranschluss kommt im November.", "note")
        await asyncio.sleep(0)
        evs = col.drain("memory.changed")
    finally:
        col.close()
    assert evs and evs[-1].nodes[0] == f"memory:{r['id']}" and evs[-1].data["change"] == "added"


async def test_project_facets_are_not_duplicates(mem: MemoryService) -> None:
    a = await mem.remember("Westside Taxi – Logo und Fahrzeugbilder", "Logo liegt als SVG vor.", "project")
    b = await mem.remember("Westside Taxi – Liefertermin Website", "Lieferung am Freitag.", "project")
    assert a["id"] != b["id"] and await mem.find_duplicates() == []


async def test_tidy_rewrites_but_refuses_information_loss(mem: MemoryService) -> None:
    r = await mem.remember("Müller Gebäudereinigung", "Neuer Kunde. Website für 499 Euro.", "customer")
    await mem.remember("Müller Gebäudereinigung", "Ansprechpartner ist Max Müller aus Soltau.", "customer")
    mem.llm.answers.append({"content": "Neuer Kunde."})  # drops facts -> must not be applied verbatim
    await mem.tidy(uuid.UUID(r["id"]))
    body = mem.vault.read(r["path"]).body
    assert "499 Euro" in body and "Max Müller" in body
    mem.llm.answers.append({"content": "Neuer Kunde, Website für 499 Euro. Ansprechpartner ist Max Müller aus Soltau."})
    await mem.tidy(uuid.UUID(r["id"]))
    body = mem.vault.read(r["path"]).body
    assert "Ergänzung" not in body.split("## Verlauf")[0] and "Max Müller" in body and "499" in body
