from pathlib import Path

import pytest

from jarvis.tools.approval_mode import ApprovalPolicy


def decisions(p: ApprovalPolicy, tainted: bool = False) -> dict[str, str]:
    return {r: p.decide("some_tool", r, tainted)[0] for r in ("read", "low", "medium", "high", "critical")}


def test_default_is_block_dangerous(tmp_path: Path) -> None:
    p = ApprovalPolicy(tmp_path / "m.json")
    assert p.mode == "block_dangerous"
    assert decisions(p) == {"read": "auto", "low": "auto", "medium": "auto", "high": "ask", "critical": "ask"}


def test_yolo_asks_nothing_except_tainted_critical(tmp_path: Path) -> None:
    p = ApprovalPolicy(tmp_path / "m.json")
    p.update({"mode": "yolo"})
    assert set(decisions(p).values()) == {"auto"}
    assert decisions(p, tainted=True) == {
        "read": "auto",
        "low": "auto",
        "medium": "auto",
        "high": "auto",
        "critical": "ask",
    }


def test_block_everything_only_reads_run(tmp_path: Path) -> None:
    p = ApprovalPolicy(tmp_path / "m.json")
    p.update({"mode": "block_everything"})
    assert decisions(p) == {"read": "auto", "low": "ask", "medium": "ask", "high": "ask", "critical": "ask"}


def test_taint_makes_strict_modes_ask(tmp_path: Path) -> None:
    p = ApprovalPolicy(tmp_path / "m.json")
    assert decisions(p, tainted=True)["low"] == "ask"


def test_custom_levels_and_tool_overrides(tmp_path: Path) -> None:
    p = ApprovalPolicy(tmp_path / "m.json")
    p.update(
        {
            "mode": "custom",
            "custom_levels": {"low": "auto", "medium": "ask", "high": "ask", "critical": "ask"},
            "tool_overrides": {"mac_type_text": "auto", "gmail_*": "deny"},
            "taint_strict": False,
        }
    )
    assert p.decide("calendar_create", "medium", False)[0] == "ask"
    assert p.decide("mac_type_text", "high", False)[0] == "auto"
    assert p.decide("gmail_send", "high", False)[0] == "deny"
    assert p.decide("mac_type_text", "high", True)[0] == "auto"  # taint_strict off
    assert p.decide("mac_type_text", "critical", True)[0] == "ask"  # tainted critical always asks


def test_persisted_and_validated(tmp_path: Path) -> None:
    f = tmp_path / "m.json"
    ApprovalPolicy(f).update({"mode": "yolo"})
    assert ApprovalPolicy(f).mode == "yolo"
    with pytest.raises(ValueError):
        ApprovalPolicy(f).update({"mode": "everything_goes"})
    with pytest.raises(ValueError):
        ApprovalPolicy(f).update({"custom_levels": {"low": "deny"}})
    assert ApprovalPolicy(f).mode == "yolo"
