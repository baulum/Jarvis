"""Concept loop, QA against a real file, e-mail (owner without approval, others only with), autopost gating."""

from __future__ import annotations

import base64
import email
import shutil
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.connectors.google import GoogleConnector
from jarvis.jobs.engine import StepSkip
from jarvis.social import emailer, qa, sfx
from jarvis.social import storyboard as sb
from jarvis.social.brand import load_brand, load_inspiration
from jarvis.social.concept import ConceptRejected, generate
from jarvis.social.media import loudness, probe
from jarvis.social.platforms import InstagramConnector
from jarvis.social.variety import Past
from jarvis.tools.registry import ToolContext

from .conftest import OWNER, SYSTEM, board, captions_ok
from .test_social_schedule import ctx_for

TODAY = date(2026, 10, 28)


def answer(category: str = "B", key: str = "captcha-kunde-2011", **board_over: Any) -> dict[str, Any]:
    b = board(**board_over)
    return {
        "concept": {"key": key, "title": b["title"], "category": category, "summary": "Fake-Fehler, dann echter Code.",
                    "idea": "…", "hook": "ERROR 404: Kundenvertrauen nicht gefunden", "tone": "humorvoll", "inspiration": ["B01"]},
        "script": "Szene 1: Fehlermeldung …",
        "storyboard": {k: v for k, v in b.items() if k not in ("version", "category", "tone")},
    }  # fmt: skip


class Asker:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        return self.answers.pop(0)


async def test_concept_rule_violations_go_back_to_the_llm() -> None:
    yesterday = Past(day=TODAY - timedelta(days=1), category="B", concept_key="anderes", hook="Anders", tone="provokant", palette="canvas")
    ask = Asker(answer(category="B"), answer(category="D"))
    res = await generate(ask, load_brand(), load_inspiration(), [yesterday], TODAY)
    assert res.attempts == 2 and res.concept["category"] == "D"
    assert "Kategorie B lief gestern" in ask.prompts[1]  # the violation was fed back
    assert res.storyboard["facts"][0]["id"] == "price_from"  # only facts used by a scene are passed to Remotion
    assert "INSPIRATION (60 Formate)" in ask.prompts[0] and "forbidden" not in ask.prompts[0]


async def test_concept_gives_up_after_bounded_attempts() -> None:
    bad = answer()
    bad["storyboard"]["scenes"] = bad["storyboard"]["scenes"][:-1]  # no end card
    with pytest.raises(ConceptRejected):
        await generate(Asker(bad, bad, bad), load_brand(), load_inspiration(), [], TODAY)


# ------------------------------------------------------------------ QA
async def fake_vision(prompt: str, frames: Path) -> dict[str, Any]:
    assert len(list(frames.glob("*.png"))) == 6  # 5 scenes + last frame
    return {"ok": True, "issues": [], "spelling_fixes": []}


async def test_qa_passes_on_a_real_render(social: SimpleNamespace, tiny_video: Path, tmp_path: Path) -> None:
    social.svc._ask_vision = fake_vision
    b = board(facts=[{"id": "price_from", "value": "499 €", "label": "Festpreis für einen Onepager. Einmalig, kein Abo."}])
    report = await social.svc.run_qa(tiny_video, b, tmp_path / "frames", {"price_from"})
    failed = [c for c in report.checks if not c["ok"]]
    assert report.passed, failed
    assert report.probe["width"] == 1080 and report.probe["video_codec"] == "h264" and report.probe["audio_codec"] == "aac"
    assert {c["id"] for c in report.checks} >= {"resolution", "fps", "duration", "loudness", "true_peak", "contrast", "reading_time", "cta", "spelling", "layout", "blank_frames"}


async def test_qa_detects_silence_wrong_format_and_spelling(social: SimpleNamespace, silent_video: Path, tmp_path: Path) -> None:
    info = await probe("ffprobe", silent_video)
    loud = await loudness("ffmpeg", silent_video)
    report = qa.QaReport()
    qa.technical(report, info, loud, board(), 19 * 1024 * 1024)
    failed = {c["id"] for c in report.checks if not c["ok"]}
    assert {"resolution", "fps", "audio_present"} <= failed and "duration" not in failed
    assert "audio_gain" in report.fixes

    async def vision(prompt: str, frames: Path) -> dict[str, Any]:
        return {"ok": False, "issues": [{"scene_id": "s2", "severity": "blocker", "kind": "spelling", "detail": "Baukasten falsch"}],
                "spelling_fixes": [{"scene_id": "s2", "old": "Kein Baukasten.", "new": "Kein Baukästen."}]}  # fmt: skip

    report = qa.QaReport()
    await qa.visual(report, "ffmpeg", silent_video, board(), tmp_path / "f", vision)
    assert not report.passed and "spelling" in report.fixes
    fixed, n = qa.apply_spelling(board(), report.spelling_fixes)
    assert n == 1 and fixed["scenes"][1]["props"]["lines"][1]["text"] == "Kein Baukästen."
    assert fixed["scenes"][1]["component"] == "kinetic_type"  # structure untouched


def test_contrast_and_frame_times() -> None:
    assert round(qa.contrast("#ffffff", "#0b0c0e"), 1) > 18
    assert qa.contrast("#2651f0", "#e3e5e8") >= 4.5  # accent text on canvas
    times = qa.frame_times(board())
    assert times[0][0] == "s1" and abs(times[0][1] - 1.8 * 0.82) < 0.01 and times[-1][0] == "end"


def test_sound_cues_follow_the_cuts() -> None:
    cues = sfx.cues_for(board())
    starts = {"s2": 1.8, "s3": 3.4, "s4": 5.6, "s5": 8.2}
    assert any(c.sound == "glitch" and abs(c.at - starts["s2"]) < 0.01 for c in cues)  # transition glitch at the cut
    assert any(c.sound == "error_buzz" and abs(c.at - 0.1) < 0.01 for c in cues)
    assert any(c.sound == "success" and abs(c.at - (starts["s5"] + 0.4)) < 0.01 for c in cues)
    argv = sfx.mix_command("ffmpeg", Path("in.mp4"), Path("out.mp4"), cues, "pulse", sb.total_seconds(board()))
    joined = " ".join(argv)
    assert "loudnorm=I=-14.0" in joined and "-c:v copy" in joined and "+faststart" in joined and "-c:a aac" in joined


# ------------------------------------------------------------------ e-mail
async def ready_video(social: SimpleNamespace, video: Path) -> uuid.UUID:
    svc = social.svc
    svc.library_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video, svc.library_dir / "2026-10-28_captcha_abcd.mp4")
    return await svc.repo.create(
        status="ready", trigger="schedule", title="Captcha für Kunden von 2011", category="B", tone="humorvoll",
        concept={"summary": "Fake-Fehler, dann echter Code.", "hook": "ERROR 404"}, captions=captions_ok(),
        file_name="2026-10-28_captcha_abcd.mp4", size=video.stat().st_size, duration_s=10.8, storyboard=board(),
    )  # fmt: skip


def test_mail_content() -> None:
    from datetime import datetime

    v = {"title": "Captcha <für> Kunden", "category": "B", "tone": "humorvoll", "concept": {"summary": "S", "hook": "H"},
         "captions": captions_ok(), "duration_s": 10.8, "size": 5_000_000, "qa": {"probe": {"width": 1080, "height": 1920}}}  # fmt: skip
    mail = emailer.build(v, day=datetime(2026, 10, 28), attachment=None, download_url="https://jarvis.example/api/social/download/t",
                         link_hours=72, publications=[], autopost_enabled=False)  # fmt: skip
    assert mail.subject == "PJE Social · 28.10.2026 · Captcha <für> Kunden"
    assert "&lt;für&gt;" in mail.html and "<für>" not in mail.html  # escaped
    for pf in ("instagram", "tiktok", "facebook", "youtube"):
        assert captions_ok()[pf]["hashtags"][0] in mail.text
    assert "YouTube-Titel: ERROR 404" in mail.text and "Download (gültig 72 h)" in mail.text
    assert "https://jarvis.example/api/social/download/t" in mail.html and "Autopost aus" in mail.text


async def test_email_to_sir_runs_without_approval(social: SimpleNamespace, tiny_video: Path) -> None:
    vid = await ready_video(social, tiny_video)
    res = await social.executor.execute("social_email_video", {"video_id": str(vid)}, ToolContext(principal=SYSTEM, idempotency_key="k1"))
    assert res.status == "ok", res.message
    [sent] = social.google.sent
    assert sent["to"] == "paulhoflich@gmail.com" and sent["attachments"][0][1] == "video/mp4"
    assert (sent["html"].startswith("<!doctype html>") and "Captions" in sent["body"]) or "CAPTIONS" in sent["html"]
    v = await social.svc.repo.get(vid)
    assert v is not None and v.email["status"] == "sent" and v.email["attached"] is True
    # same idempotency key again: the ledger answers, no second mail
    again = await social.executor.execute("social_email_video", {"video_id": str(vid)}, ToolContext(principal=SYSTEM, idempotency_key="k1"))
    assert again.status in ("ok", "duplicate") and len(social.google.sent) == 1


async def test_email_to_someone_else_needs_approval(social: SimpleNamespace, tiny_video: Path) -> None:
    vid = await ready_video(social, tiny_video)
    res = await social.executor.execute("social_email_video", {"video_id": str(vid), "to": "kunde@example.com"}, ToolContext(principal=OWNER))
    assert res.status == "approval_required" and social.google.sent == []
    social.svc.settings.save({"email_recipient": "bagy@example.com"})  # a configured third-party recipient, too
    res = await social.executor.execute("social_email_video", {"video_id": str(vid)}, ToolContext(principal=SYSTEM))
    assert res.status == "approval_required" and social.google.sent == []


async def test_large_video_goes_out_as_link(social: SimpleNamespace, tiny_video: Path) -> None:
    social.state.settings.server.public_base_url = "https://jarvis.example"
    social.svc.cfg.email_attachment_max_mb = 0.01
    vid = await ready_video(social, tiny_video)
    await social.svc.send_email(vid, "paulhoflich@gmail.com")
    [sent] = social.google.sent
    assert sent["attachments"] is None and "https://jarvis.example/api/social/download/" in sent["html"]


async def test_gmail_sends_html_with_text_fallback_and_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = GoogleConnector.__new__(GoogleConnector)
    captured: dict[str, Any] = {}

    async def api(method: str, url: str, **kw: Any) -> Any:
        captured.update(kw["json"])
        return {"id": "m1"}

    monkeypatch.setattr(conn, "_api", api, raising=False)
    await conn.send_mail("paulhoflich@gmail.com", "Betreff", "Text", attachments=[("v.mp4", "video/mp4", b"\x00\x01")], html="<p>HTML</p>")
    msg = email.message_from_bytes(base64.urlsafe_b64decode(captured["raw"]))
    types = [p.get_content_type() for p in msg.walk()]
    assert types[0] == "multipart/mixed" and "multipart/alternative" in types
    assert "text/plain" in types and "text/html" in types and "video/mp4" in types


# ------------------------------------------------------------------ autopost
async def test_autopost_is_off_by_default_and_needs_connection_and_switches(social: SimpleNamespace) -> None:
    svc = social.svc
    assert not any(svc.can_autopost(pf) for pf in ("instagram", "facebook", "tiktok", "youtube"))
    vid = await svc.repo.create(status="ready")
    with pytest.raises(StepSkip, match="Autopost aus"):
        await svc.step_publish(ctx_for(social, {}, {"video_id": str(vid)}))

    ig = InstagramConnector(social.state)
    social.state.connectors.connectors["social_instagram"] = ig
    svc.settings.save({"autopost_enabled": True, "platforms": {"instagram": {"autopost": True}}})
    assert not svc.can_autopost("instagram")  # switched on, but connector disabled and not connected
    ig.enabled = True
    assert not svc.can_autopost("instagram")  # still not connected
    social.secrets["meta-oauth-token"] = '{"page_token": "x", "page_id": "1", "ig_user_id": "2"}'
    assert svc.can_autopost("instagram")
    svc.settings.save({"autopost_enabled": False})
    assert not svc.can_autopost("instagram")  # global switch wins
    svc.settings.save({"autopost_enabled": True, "platforms": {"instagram": {"autopost": False}}})
    assert not svc.can_autopost("instagram")  # per-platform switch wins


async def test_publishing_always_needs_an_approval(social: SimpleNamespace, tiny_video: Path) -> None:
    vid = await ready_video(social, tiny_video)
    res = await social.executor.execute("social_publish_video", {"video_id": str(vid), "platform": "youtube"}, ToolContext(principal=SYSTEM))
    assert res.status == "approval_required"
    assert social.registry.get("social_publish_video").risk == "high"
