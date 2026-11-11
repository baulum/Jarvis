import uuid
from pathlib import Path

import pytest

from jarvis.config import get_settings
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault

pytestmark = pytest.mark.integration


@pytest.fixture
async def mem(tmp_path: Path):
    s = get_settings()
    embedder = LocalEmbedder(s.paths.data_dir / "models" / "fastembed", s.llm.embedding_model)
    from types import SimpleNamespace

    from jarvis.agent.claude_agent import ClaudeOneShot

    oneshot = ClaudeOneShot(SimpleNamespace(settings=s))  # type: ignore[arg-type]

    async def structured(system: str, prompt: str, schema: dict) -> object:
        return await oneshot.run(prompt, schema=schema, system=system)

    svc = MemoryService(Vault(tmp_path / "vault"), embedder, structured)
    svc.vault.ensure()
    yield svc


async def test_remember_dedup_search_correct_forget(mem: MemoryService) -> None:
    a = await mem.remember(
        "Präferenz Rechnungsversand", "Sir möchte Rechnungen immer als PDF per E-Mail versenden.", "preference"
    )
    assert a["status"] == "created" and (mem.vault.root / a["path"]).exists()
    dup = await mem.remember(
        "Präferenz Rechnungsversand", "Sir möchte Rechnungen immer als PDF per E-Mail versenden.", "preference"
    )
    assert dup["status"] == "duplicate"
    upd = await mem.remember("Präferenz Rechnungsversand", "Zahlungsziel standardmäßig 14 Tage.", "preference")
    assert upd["status"] == "updated" and "14 Tage" in (mem.vault.root / a["path"]).read_text()
    await mem.remember(
        "Müller Gebäudereinigung", "Kunde aus Soltau, Ansprechpartner Herr Müller.", "customer", entities=["Soltau"]
    )
    hits = await mem.search("Wie verschicke ich Rechnungen?", limit=3)
    assert hits[0]["title"] == "Präferenz Rechnungsversand"
    corr = await mem.correct(uuid.UUID(a["id"]), "Rechnungen gehen ab sofort per Post raus.", "pytest")
    assert corr["status"] == "corrected"
    active = await mem.search("Rechnungsversand", limit=5)
    assert all(h["status"] == "active" for h in active)
    assert any("Post" in h["content"] for h in active)
    graph = await mem.graph()
    assert any(n["label"] == "Soltau" for n in graph["nodes"]) and graph["edges"]
    gone = await mem.forget(uuid.UUID(corr["new_id"]), "pytest")
    assert not (mem.vault.root / gone["path"]).exists()


async def test_sensitive_requires_confirmation(mem: MemoryService) -> None:
    r = await mem.remember("Bankverbindung", "IBAN DE89 3704 0044 0532 0130 00", "note")
    assert r["status"] == "pending_confirmation"
    assert not list(mem.vault.root.rglob("Bankverbindung*.md"))  # nothing on disk before consent
    done = await mem.confirm_pending(uuid.UUID(r["id"]), True, "pytest")
    assert done["status"] == "created"
    text = (mem.vault.root / done["path"]).read_text()
    assert "sensitivity: sensitive" in text


async def test_external_edit_is_reindexed(mem: MemoryService) -> None:
    r = await mem.remember("Server Zugang Doku", "Der Staging-Server läuft auf Port 8443.", "technical")
    p = mem.vault.root / r["path"]
    p.write_text(p.read_text().replace("8443", "9443"))
    await mem.reindex_all()
    hits = await mem.search("Staging-Server Port", limit=1)
    assert "9443" in hits[0]["content"]


async def test_llm_extraction(mem: MemoryService) -> None:
    facts = await mem.extract(
        "Merke dir bitte: Unser neuer Kunde heißt Bäckerei Schmidt aus Celle, Ansprechpartnerin ist Frau Schmidt.",
        "Notiert, Sir.",
    )
    assert facts and any("Schmidt" in f["title"] or "Schmidt" in f["content"] for f in facts)


async def test_llm_extraction_updates_existing_entity(mem: MemoryService) -> None:
    """Real model: a new fact about a known customer extends that note instead of creating a duplicate."""
    base = await mem.remember("Bäckerei Schmidt", "Kunde aus Celle. Ansprechpartnerin ist Frau Schmidt.", "customer")
    facts = await mem.extract(
        "Bei der Bäckerei Schmidt ist ab sofort Herr Becker unser Ansprechpartner, Frau Schmidt ist in Rente.",
        "Verstanden, Sir.",
    )
    assert facts
    for f in facts:
        await mem.apply_fact(f, {"session_id": "pytest"})
    customers = await mem.list_items(kind="customer", status="active")
    assert customers["total"] == 1, [i["title"] for i in customers["items"]]
    hits = await mem.search("Wer ist Ansprechpartner bei der Bäckerei Schmidt?", limit=2)
    assert "Becker" in hits[0]["content"]
    assert hits[0]["id"] == base["id"] or (await mem.get(uuid.UUID(base["id"])))["status"] == "superseded"


# ------------------------------------------------------------------ behaviour rules
async def test_rules_create_replace_deactivate_and_migrate(mem: MemoryService) -> None:
    r = await mem.remember("Coding-Modell", "Coding-Aufträge laufen fest mit Sonnet 5.5.", "rule", actor="pytest")
    assert r["status"] == "created" and r["path"].startswith("Regeln/")
    assert [x.title for x in mem.rules.effective()] == ["Coding-Modell"]
    dup = await mem.save_rule("Anders benannt", "Coding-Aufträge laufen fest mit Sonnet 5.5.")
    assert dup["status"] == "duplicate"  # the same rule is not stored twice
    proj = await mem.remember(
        "Lead-Tool Tests", "Immer npm test ausführen.", "rule", scope="project", project="Lead-Tool"
    )
    assert proj["status"] == "created"
    glob = [x for x in mem.rules.effective() if x.scope == "global"]
    assert [x.title for x in glob] == ["Coding-Modell"]

    # correcting a rule: the new one is in force, the old one stays visible but is replaced; scope carries over
    corr = await mem.correct(uuid.UUID(r["id"]), "Coding-Aufträge laufen fest mit Opus 5.5.", "pytest")
    assert corr["status"] == "corrected"
    live = {x.title: x for x in mem.rules.effective()}
    assert (
        "Coding-Modell" in live and "Opus 5.5" in live["Coding-Modell"].text and live["Coding-Modell"].scope == "global"
    )
    old = [x for x in mem.rules.all() if x.superseded]
    assert len(old) == 1 and "Sonnet" in old[0].text and not old[0].in_force
    assert live["Coding-Modell"].supersedes == r["id"]

    # deactivating removes it from the prompt, reactivating brings it back, nothing is deleted
    await mem.update_rule(live["Coding-Modell"].id, "pytest", active=False)
    assert "Coding-Modell" not in {x.title for x in mem.rules.effective()}
    assert any(x.title == "Coding-Modell" and not x.active for x in mem.rules.all())
    await mem.update_rule(live["Coding-Modell"].id, "pytest", active=True)
    assert "Coding-Modell" in {x.title for x in mem.rules.effective()}

    # rules never enter the "maybe relevant" block (they are in the system prompt) and hold no credentials
    assert "Opus 5.5" not in await mem.context_for("Welches Modell für Coding-Aufträge?", include_sensitive=True)
    with pytest.raises(ValueError):
        await mem.save_rule("Zugang", "Das Passwort: geheim123 immer verwenden")
    with pytest.raises(ValueError):
        await mem.save_rule("Ohne Projekt", "Etwas Wichtiges", scope="project")


async def test_preference_migration_through_the_service(mem: MemoryService) -> None:
    from jarvis.memory.vault import Note

    for title, text, ts in (
        ("Modell alt", "Coding-Aufträge laufen immer mit Sonnet 5.5.", "2026-02-01T10:00:00+00:00"),
        ("Modell neu", "Coding-Aufträge laufen fest mit Opus 5.5.", "2026-05-01T10:00:00+00:00"),
    ):
        mem.vault.write(Note(title=title, body=text, kind="preference", created=ts, updated=ts))
    dry = await mem.migrate_rules(dry_run=True)
    assert dry["dry_run"] and len(dry["candidates"]) == 2 and not mem.rules.all()
    res = await mem.migrate_rules()
    assert res["migrated"] == 2
    assert [x.title for x in mem.rules.effective()] == ["Modell neu"]
    assert (await mem.migrate_rules())["migrated"] == 0  # idempotent
    assert (mem.vault.root / "Präferenzen" / "Modell alt.md").exists()  # nothing deleted
