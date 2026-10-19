import json
import time
from pathlib import Path

from jarvis.agent.model_router import ModelRouter, parse_command
from jarvis.agent.usage import UsageTracker, model_alias


def _info(five: float, seven: float = 0.0, resets_in: int = 3600) -> dict:
    now = int(time.time())
    return {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "overageStatus": "rejected",
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": five, "resetsAt": now + resets_in},
            "seven_day": {"utilization": seven, "resetsAt": now + 86400},
        },
    }


def test_rate_limit_event_is_parsed_and_persisted(tmp_path: Path) -> None:
    t = UsageTracker(tmp_path / "u.json")
    assert not t.status()["known"]
    t.on_rate_limit(_info(0.42, 0.1))
    st = t.status()
    assert st["known"] and st["windows"]["five_hour"]["utilization"] == 0.42
    assert 3500 < st["windows"]["five_hour"]["resets_in_s"] <= 3600
    assert UsageTracker(tmp_path / "u.json").status()["windows"]["seven_day"]["utilization"] == 0.1  # survives restart


def test_expired_window_counts_as_empty(tmp_path: Path) -> None:
    t = UsageTracker(tmp_path / "u.json")
    t.on_rate_limit(_info(0.9, resets_in=-10))
    w = t.status()["windows"]["five_hour"]
    assert w["stale"] and w["utilization"] == 0.0 and t.pressure() == 0.0


def test_token_counters_per_model(tmp_path: Path) -> None:
    t = UsageTracker(tmp_path / "u.json")
    t.on_result("claude-sonnet-5-5", {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100})
    t.on_result("claude-sonnet-5-5", {"input_tokens": 1, "output_tokens": 2})
    t.on_result("claude-haiku-5-5", {"output_tokens": 7})
    today = t.status()["today"]
    assert today["sonnet"]["turns"] == 2 and today["sonnet"]["output_tokens"] == 7
    assert today["haiku"]["output_tokens"] == 7
    assert model_alias("claude-opus-5-5") == "opus" and model_alias(None) == "other"
    json.loads((tmp_path / "u.json").read_text())


async def test_warning_fires_once_per_window(tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []

    async def notify(level: str, title: str, body: str) -> None:
        sent.append((level, title))

    t = UsageTracker(tmp_path / "u.json")
    t.notify = notify
    info = _info(0.85)
    t.on_rate_limit(info)
    t.on_rate_limit(info)
    import asyncio

    await asyncio.sleep(0.05)
    assert len(sent) == 1 and "85" in sent[0][1]
    t.on_rate_limit({**info, "unifiedWindows": {"five_hour": {**info["unifiedWindows"]["five_hour"], "utilization": 0.96}}})
    await asyncio.sleep(0.05)
    assert len(sent) == 2 and sent[1][0] == "critical"


def test_router_steps_down_only_for_automatic_choices(tmp_path: Path) -> None:
    r = ModelRouter("opus", False, tmp_path / "m.json")
    r.usage = UsageTracker(tmp_path / "u.json")
    assert r.choose("hallo", "desktop", "s1")[0] == "opus"
    r.usage.on_rate_limit(_info(0.93))
    model, reason = r.choose("hallo", "desktop", "s1")
    assert model == "sonnet" and "Kontingent" in reason
    r.session_overrides["s1"] = "opus"  # an explicit choice is always honoured
    assert r.choose("hallo", "desktop", "s1")[0] == "opus"
    r.session_overrides.clear()
    r.set("sonnet", "default")
    r.usage.on_rate_limit(_info(0.98))
    assert r.choose("plane meine Strategie ausführlich", "desktop", "s2")[0] == "haiku"


def test_apply_turn_scope(tmp_path: Path) -> None:
    r = ModelRouter("sonnet", True, tmp_path / "m.json")
    r.apply("opus", "turn", "s1")
    assert r.choose("hi", "desktop", "s1")[0] == "opus"
    assert r.choose("hi", "desktop", "s1")[0] != "opus"  # only the next answer


def test_more_ways_to_ask_for_a_model() -> None:
    for text, model in [
        ("Claude, nutze Haiku", "haiku"),
        ("Haiku nutzen bitte", "haiku"),
        ("stell bitte auf Sonnet um", "sonnet"),
        ("wechsle zu Opus", "opus"),
        ("Claude Opus bitte", "opus"),
        ("ich möchte mit Haiku weitermachen", "haiku"),
    ]:
        cmd = parse_command(text)
        assert cmd is not None and cmd.model == model, text
    turn = parse_command("nur diesmal Opus: analysiere meine Rechnungen genau")
    assert turn is not None and turn.scope == "turn" and turn.remainder.startswith("analysiere")
    assert parse_command("Was ist der Unterschied zwischen Opus und Sonnet?") is None
