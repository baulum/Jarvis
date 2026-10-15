import uuid
from pathlib import Path

import pytest

from jarvis.agent.llm import OllamaClient
from jarvis.config import get_settings
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault

pytestmark = pytest.mark.integration


@pytest.fixture
async def mem(tmp_path: Path):
    s = get_settings()
    client = OllamaClient(s.llm.ollama_url)
    svc = MemoryService(Vault(tmp_path / "vault"), client, s.llm.embedding_model, s.llm.utility_model)
    svc.vault.ensure()
    yield svc
    await client.close()


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
