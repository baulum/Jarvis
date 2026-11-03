"""Company profiles (several brands, each with its own schedule, memory, library, recipients and accounts) and the
priority of Sir's input in the "Produzieren" field over the profile defaults (price, offer, topic)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.social import captions as cap
from jarvis.social import qa
from jarvis.social import storyboard as sb
from jarvis.social.api import require_sir, router
from jarvis.social.brand import brand_for, load_brand, load_inspiration
from jarvis.social.companies import DEFAULT_ID
from jarvis.social.concept import build_prompt, system_prompt, validate_for
from jarvis.social.service import DAILY_KEY, PLAYBOOK
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext

from .conftest import OWNER, SYSTEM, board, captions_ok
from .test_social_pipeline import Asker, answer
from .test_social_schedule import ctx_for

TODAY = date(2026, 11, 3)
HINT = "Angebot für 299 €"
APO = {
    "description": "HR-Software als Multi-Tenant-SaaS",
    "website": "apo-hr.example",
    "cta_button": "Demo anfragen",
    "offer": "Alle HR-Prozesse in einer Plattform",
    "price": "49 €",
    "colors": {"accent": "#0f9d58"},
    "hashtags": ["HRSoftware", "#SaaS"],
}


def pje(svc: Any) -> dict[str, Any]:
    return svc.company(DEFAULT_ID)


def with_price(price_fact: str, text: str = "Festpreis.") -> dict[str, Any]:
    """An LLM answer whose storyboard shows ``text`` and the fact ``price_fact``."""
    a = answer()
    scenes = a["storyboard"]["scenes"]
    scenes[1]["props"]["lines"] = [{"text": text}, {"text": "Kein Baukasten.", "accent": True}]
    scenes[3]["props"]["fact_id"] = price_fact
    return a


# ------------------------------------------------------------------ migration of the single-brand setup
async def test_pje_is_migrated_as_first_profile_without_changing_behaviour(social: SimpleNamespace) -> None:
    svc = social.svc
    data = svc.companies.load()
    assert [c["id"] for c in data["companies"]] == [DEFAULT_ID, "apo-hr"] and data["active"] == DEFAULT_ID
    p = pje(svc)
    assert p["autopost"] is False and p["official_api"] is True and p["brand_file"] == "brand.yaml"
    assert svc.video_recipients(p) == ["paulhoflich@gmail.com", "bagy2014@gmail.com"]
    old, new = load_brand(), brand_for(p)
    assert new.facts == old.facts and new.active_offers(TODAY) == old.active_offers(TODAY)
    assert (new.website, new.cta_button, new.company) == (old.website, old.cta_button, old.company)
    assert new.allowed_prices(TODAY) == old.allowed_prices(TODAY) and "499 €" in new.allowed_prices(TODAY)
    assert system_prompt(new).startswith(
        "Du bist Creative Director und Motion Designer für PJE Systems, eine kleine Webdesign-Agentur aus Wolnzach."
    )
    apo = svc.company("Apo HR")  # example profile: Sir fills in the rest himself
    assert apo["description"] == "HR-Software als Multi-Tenant-SaaS" and apo["autopost"] is False
    with pytest.raises(ToolUserError, match=r"unvollständig.*Website"):
        await svc.produce(SYSTEM, company="Apo HR")


# ------------------------------------------------------------------ Sir's input wins: 299 € over the stored 499 €
def test_hint_price_overrides_the_default_price_everywhere() -> None:
    """Sir writes „Angebot für 299 €“ – the stored 499 € may only be a fallback, nowhere a competitor."""
    default = load_brand()
    brand = default.with_hint(HINT)

    # brief: the input leads the prompt, the default price is gone from offers and facts
    prompt = build_prompt(brand, load_inspiration(), [], TODAY)
    assert prompt.startswith("VORRANG – VORGABE VON SIR") and "299 €" in prompt.split("\n\n")[0]
    offers_line = next(ln for ln in prompt.splitlines() if ln.startswith("Gültige Angebote"))
    assert "299 €" in offers_line and "499" not in offers_line
    assert [f["value"] for f in brand.facts if "€" in f["value"]] == ["299 €"]
    assert {f["id"] for f in brand.facts} >= {"hint_price"} and "price_from" not in brand.fact_ids
    assert brand.required_prices() == ["299 €"] and {cap_amount(p) for p in brand.allowed_prices(TODAY)} == {"299"}

    # storyboard + rendered scenes: 499 € is rejected, 299 € (text or fact scene) accepted
    old = with_price("price_from")
    old_board = sb.normalize(old["storyboard"], title="t", category="B", tone="humorvoll")
    old_board["facts"] = default.facts
    problems = validate_for(old_board, brand, TODAY).problems
    assert any("price_from" in p for p in problems) and any("299 €" in p for p in problems)
    shown_old = sb.normalize(with_price("reply_time", "Nur 499 €.")["storyboard"], title="t", category="B", tone="humorvoll")
    shown_old["facts"] = [f for f in brand.facts if f["id"] == "reply_time"]
    assert any("„499 €“ ist nicht erlaubt" in p for p in validate_for(shown_old, brand, TODAY).problems)
    new_board = sb.normalize(with_price("hint_price")["storyboard"], title="t", category="B", tone="humorvoll")
    new_board["facts"] = [f for f in brand.facts if f["id"] == "hint_price"]
    assert validate_for(new_board, brand, TODAY).ok, validate_for(new_board, brand, TODAY).problems

    # QA check on the rendered storyboard
    report = qa.QaReport()
    qa.textual(report, shown_old, brand.fact_ids, brand.allowed_prices(TODAY), brand.required_prices())
    assert next(c for c in report.checks if c["id"] == "prices")["ok"] is False and "storyboard" in report.fixes
    report = qa.QaReport()
    qa.textual(report, new_board, brand.fact_ids, brand.allowed_prices(TODAY), brand.required_prices())
    assert next(c for c in report.checks if c["id"] == "prices")["ok"] is True

    # captions: the default 499 € is refused, every caption has to name 299 €
    allowed, required = brand.allowed_prices(TODAY), brand.required_prices()
    assert any("„499 €“" in p for p in cap.validate(captions_ok(), allowed, required).problems)
    good = captions_ok()
    good["facebook"]["text"] = "Eigenes Layout, echter Code – jetzt für 299 €. Projekt anfragen auf pje-systems.de."
    assert any(p.startswith("instagram:") and "299 €" in p for p in cap.validate(good, allowed, required).problems)
    for pf in ("instagram", "tiktok"):
        good[pf]["text"] += " Jetzt 299 €."
    good["youtube"]["description"] += " Für 299 €."
    assert cap.validate(good, allowed, required).ok, cap.validate(good, allowed, required).problems
    assert "299 €" in cap.prompt({"title": "t"}, "s", brand, TODAY).split("\n")[1]

    # without input the profile default stays the fallback
    assert "499 €" in default.allowed_prices(TODAY) and default.required_prices() == []


def cap_amount(p: str) -> str:
    from jarvis.social.prices import amount

    return amount(p)


async def test_hint_price_reaches_storyboard_and_captions_of_a_production(social: SimpleNamespace) -> None:
    """Through the real pipeline steps: hint → concept (fact scene) → storyboard check → QA prices → captions."""
    svc = social.svc
    ctx = ctx_for(social, {"trigger": "manual", "hint": HINT, "company": DEFAULT_ID})
    await svc.step_start(ctx)
    vid = uuid.UUID(ctx.context["video_id"])
    asker = Asker(with_price("price_from"), with_price("hint_price"))  # 1st draft keeps 499 €, gets sent back
    svc._ask_concept = asker
    await svc.step_concept(ctx)
    assert "VORRANG" in asker.prompts[0] and "fact_id 'price_from' gibt es nicht" in asker.prompts[1]
    v = await svc.repo.get(vid)
    assert v is not None and v.concept["hint"] == HINT
    assert [f["value"] for f in v.storyboard["facts"]] == ["299 €"]  # what Remotion renders in the fact scene
    assert v.storyboard["brand"]["name"] == "PJE Systems"
    assert await svc.step_storyboard(ctx)

    caps = captions_ok()
    for pf in ("instagram", "tiktok", "facebook"):
        caps[pf]["text"] = caps[pf]["text"].replace("ab 499 €", "für 299 €") + " Nur 299 €."
    caps["youtube"]["description"] += " Nur 299 €."
    svc._ask_concept = Asker(captions_ok(), caps)  # first with the default 499 € → refused
    await svc.step_captions(ctx)
    v = await svc.repo.get(vid)
    assert v is not None and all("299 €" in cap.full_text(pf, v.captions[pf]) for pf in cap.PLATFORMS)
    assert not any("499" in cap.full_text(pf, v.captions[pf]) for pf in cap.PLATFORMS)
    assert any("499 €" in e["msg"] for e in v.log)  # the refused draft is in the log


# ------------------------------------------------------------------ profiles per company
@pytest.fixture
async def api(social: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = social.state
    app.dependency_overrides[require_sir] = lambda: OWNER
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        social.client = client
        yield social


async def test_companies_can_be_added_edited_selected_and_deleted(api: SimpleNamespace) -> None:
    c = api.client
    r = await c.post("/api/social/companies", json={"name": "Bäckerei Huber", "website": "https://baeckerei.example/"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] == "backerei-huber" and new["website"] == "baeckerei.example" and new["autopost"] is False
    assert "Beschreibung/Positionierung" in new["missing"]
    assert (await c.post("/api/social/companies", json={"name": "bäckerei huber"})).status_code == 400  # duplicate
    assert (await c.put(f"/api/social/companies/{new['id']}", json={"colors": {"accent": "blau"}})).status_code == 400

    r = await c.put(f"/api/social/companies/{new['id']}", json={**APO, "name": "Bäckerei Huber"})
    assert r.status_code == 200 and r.json()["missing"] == [] and r.json()["hashtags"] == ["#HRSoftware", "#SaaS"]
    listing = (await c.get("/api/social/companies")).json()
    entry = next(x for x in listing["companies"] if x["id"] == new["id"])
    assert entry["schedule"]["exists"] and entry["schedule"]["enabled"] is False  # a new daily run starts paused
    assert listing["active"] == DEFAULT_ID

    r = await c.post(f"/api/social/companies/{new['id']}/activate")
    assert r.json()["active"] == new["id"]
    status = (await c.get("/api/social/status")).json()
    assert status["company"]["id"] == new["id"] and status["settings"]["company_id"] == new["id"]

    # settings are per company: Sir's standing wish and the schedule of this company only
    r = await c.put("/api/social/settings", json={"company": new["id"], "hint": "Brötchen-Abo", "daily_enabled": True})
    assert r.json()["hint"] == "Brötchen-Abo" and r.json()["schedule"]["enabled"] is True
    pje_settings = (await c.get("/api/social/settings", params={"company": "PJE Systems"})).json()
    assert pje_settings["hint"] == "" and pje_settings["schedule"]["time"] == "08:00"

    r = await c.post("/api/social/produce", json={"hint": "Etwas Neues"})  # default: the active profile
    assert r.status_code == 200 and api.state.tasks.created[-1]["params"]["company"] == new["id"]

    r = await c.delete(f"/api/social/companies/{new['id']}")
    assert r.json() == {"deleted": new["id"], "active": DEFAULT_ID}
    assert await api.svc.schedule(new["id"]) is None
    assert (await c.delete("/api/social/companies/apo-hr")).status_code == 200
    assert (await c.delete(f"/api/social/companies/{DEFAULT_ID}")).status_code == 400  # never the last one


async def test_daily_runs_memory_and_library_are_per_company(social: SimpleNamespace) -> None:
    svc = social.svc
    svc.companies.update("apo-hr", APO)
    pje_sched = await svc.ensure_schedule()
    apo_sched = await svc.ensure_schedule("apo-hr")
    assert pje_sched.id != apo_sched.id and apo_sched.action["daily_key"] == f"{DAILY_KEY}:apo-hr"
    assert pje_sched.action["daily_key"] == DAILY_KEY and pje_sched.name == "PJE Systems Daily Social Video"

    # one automatic video per company and day – both companies produce on the same day
    a = await svc.step_start(ctx_for(social, {"trigger": "schedule"}))
    b = await svc.step_start(ctx_for(social, {"trigger": "schedule", "company": "apo-hr"}))
    assert a["company"] == DEFAULT_ID and b["company"] == "apo-hr"
    with pytest.raises(Exception, match="Apo HR"):
        await svc.step_start(ctx_for(social, {"trigger": "schedule", "company": "apo-hr"}))

    # creative memory: Apo HR's new video only has to differ from Apo HR's videos
    await svc.repo.update(uuid.UUID(b["video_id"]), status="ready", category="B", concept_key="hr-chaos", title="HR")
    assert [p.concept_key for p in await svc.repo.history(company_id="apo-hr")] == ["hr-chaos"]
    assert await svc.repo.history() == []
    assert [str(v.id) for v in await svc.repo.list_videos(company_id="apo-hr")] == [b["video_id"]]

    # concept for Apo HR: its brand, its colours, its end card address
    svc._ask_concept = asker = Asker(_apo_answer(), _apo_answer("hr-onboarding"))
    ctx = ctx_for(social, {"trigger": "manual", "company": "apo-hr"})
    await svc.step_start(ctx)
    await svc.step_concept(ctx)
    v = await svc.repo.get(uuid.UUID(ctx.context["video_id"]))
    assert v is not None and v.storyboard["brand"] == {"name": "Apo HR", "colors": {**_apo_colors()}}
    assert "Grundkonzept „hr-chaos“" in asker.prompts[1]  # its own history was enforced
    assert "Positionierung: HR-Software als Multi-Tenant-SaaS" in asker.prompts[0]


def _apo_colors() -> dict[str, str]:
    return {"accent": "#0f9d58", "ink": "#0b0c0e", "canvas": "#e3e5e8"}


def _apo_answer(key: str = "hr-chaos") -> dict[str, Any]:
    a = answer(key=key)
    a["storyboard"]["scenes"][3]["props"]["fact_id"] = "offer_price"
    a["storyboard"]["scenes"][-1]["props"].update(button="Demo anfragen", url="apo-hr.example")
    return a


async def test_tools_take_an_optional_company(social: SimpleNamespace, tiny_video: Path) -> None:
    from .test_social_pipeline import ready_video

    svc, ex = social.svc, social.executor
    svc.companies.update("apo-hr", {**APO, "recipients": ["paulhoflich@gmail.com", "hr@apo-hr.example"]})
    res = await ex.execute("social_produce_video", {"company": "Apo HR", "hint": HINT}, ToolContext(principal=OWNER))
    assert res.status == "ok", res.message
    assert social.state.tasks.created[-1]["params"] | {"company": "apo-hr", "hint": HINT} == social.state.tasks.created[-1]["params"]
    res = await ex.execute("social_produce_video", {"company": "Gibt es nicht"}, ToolContext(principal=OWNER))
    assert res.status == "error" and "vorhanden" in res.message

    pje_vid = await ready_video(social, tiny_video)
    apo_vid = await ready_video(social, tiny_video)
    await svc.repo.update(apo_vid, company_id="apo-hr")
    res = await ex.execute("social_list_videos", {"company": "apo-hr"}, ToolContext(principal=OWNER))
    assert [v["id"] for v in res.data["videos"]] == [str(apo_vid)] and res.data["company"] == "Apo HR"
    res = await ex.execute("social_list_videos", {}, ToolContext(principal=OWNER))
    assert [v["id"] for v in res.data["videos"]] == [str(pje_vid)]  # default: the active profile (PJE)

    # e-mail: the video's company list; a recipient Sir only put into the profile still needs an approval
    res = await ex.execute("social_email_video", {"video_id": str(apo_vid)}, ToolContext(principal=SYSTEM))
    assert res.status == "approval_required" and social.google.sent == []
    res = await ex.execute(
        "social_email_video", {"video_id": str(apo_vid), "company": "PJE Systems"}, ToolContext(principal=OWNER)
    )
    assert res.status == "error" and "gehört zu Apo HR" in res.message

    # publishing: PJE's connected platform accounts are not Apo HR's
    with pytest.raises(ToolUserError, match="gehören nicht zu Apo HR"):
        await svc.publish(apo_vid, "tiktok")
    v = await svc.repo.get(apo_vid)
    assert svc.uptik_scope(v) == {"autopost": False, "accounts": [], "exclude": []}
    svc.companies.update("apo-hr", {"uptik_accounts": ["Apo TikTok"], "autopost": True})
    v_pje = await svc.repo.get(pje_vid)
    assert svc.uptik_scope(await svc.repo.get(apo_vid)) == {"autopost": True, "accounts": ["Apo TikTok"], "exclude": []}
    assert svc.uptik_scope(v_pje) == {"autopost": False, "accounts": None, "exclude": ["Apo TikTok"]}

    mail = svc.build_mail(await svc.repo.get(apo_vid), attachment=None, download_url=None)
    assert mail.subject.startswith("Apo HR Social · ") and "#0f9d58" in mail.html


def test_playbook_titles_name_the_company() -> None:
    from jarvis.social.service import playbook

    pb = playbook(SimpleNamespace(**{n: None for n in ("step_start", "step_concept", "step_storyboard", "step_render",
                                                        "step_qa", "step_captions", "step_library", "step_email",
                                                        "step_publish")}))  # type: ignore[arg-type]  # fmt: skip
    assert pb.name == PLAYBOOK and pb.title_template == "{company_name} Daily Social Video"
    assert pb.params_schema["properties"]["company_name"]["default"] == "PJE Systems"


def test_qa_contrast_follows_the_brand_colours() -> None:
    assert qa.surface_colors() == qa.SURFACE_COLORS
    assert qa.surface_colors({"accent": "#2651f0"})["light"][1] == ("#2651f0", "#e3e5e8")  # PJE unchanged
    yellow = qa.surface_colors({"accent": "#ffd400"})
    assert ("#0b0c0e", "#ffd400") in yellow["accent"]  # light accent: dark text on it, not white
    assert all(qa.contrast(fg, bg) >= qa.MIN_CONTRAST for pairs in yellow.values() for fg, bg in pairs)
    b = board(brand={"name": "X", "colors": {"accent": "#ffd400"}})
    report = qa.QaReport()
    qa.textual(report, b, {"price_from"})
    assert next(c for c in report.checks if c["id"] == "contrast")["ok"]
