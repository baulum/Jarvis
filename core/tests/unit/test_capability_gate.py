"""Capability gate: tools the host cannot run disappear with a reason – on the Mac nothing changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jarvis.config import load_permissions
from jarvis.connectors.mac import MacConnector
from jarvis.platform import LinuxAdapter, MacAdapter, WindowsAdapter
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolContext, ToolRegistry, ToolSpec

# The mac_* tool set as the agent knew it before the adapters existed (names, risks): must stay identical.
MAC_TOOLS = {
    "mac_applescript": "high",
    "mac_click": "high",
    "mac_clipboard_get": "low",
    "mac_clipboard_set": "low",
    "mac_files_mkdir": "low",
    "mac_files_move": "medium",
    "mac_files_reveal": "low",
    "mac_files_search": "read",
    "mac_frontmost": "read",
    "mac_key": "high",
    "mac_list_apps": "read",
    "mac_list_windows": "read",
    "mac_open_app": "low",
    "mac_open_url": "low",
    "mac_quit_app": "medium",
    "mac_screenshot": "low",
    "mac_shortcut_run": "medium",
    "mac_shortcuts_list": "read",
    "mac_type_text": "high",
    "mac_ui_press": "high",
    "mac_ui_tree": "read",
}


def _registry(adapter: Any | None) -> ToolRegistry:
    reg = ToolRegistry(load_permissions())
    for spec in MacConnector(SimpleNamespace()).tools():  # type: ignore[arg-type]
        reg.register(spec)

    async def noop(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return "ok"

    reg.register(ToolSpec("memory_search", "x", {"type": "object", "properties": {}}, noop, "read", "memory"))
    if adapter is not None:
        reg.set_capabilities(adapter.capabilities())
    return reg


def test_mac_tool_set_is_unchanged_on_the_mac() -> None:
    reg = _registry(MacAdapter(which=lambda n: f"/usr/bin/{n}", importable=lambda m: True))
    mac = {t.name: t.risk for t in reg.all(include_disabled=True) if t.connector == "mac"}
    assert mac == MAC_TOOLS
    enabled = {t.name for t in reg.all() if t.connector == "mac"}
    # mac_applescript stays switched off by permissions.yaml, exactly as before
    assert enabled == set(MAC_TOOLS) - {"mac_applescript"}
    assert all(reg.unavailable_reason(t) is None for t in reg.all(include_disabled=True))


def test_without_a_capability_report_the_registry_behaves_as_before() -> None:
    reg = _registry(None)
    assert {t.name for t in reg.all() if t.connector == "mac"} == set(MAC_TOOLS) - {"mac_applescript"}


def test_mac_tools_disappear_on_windows_and_linux_with_a_reason() -> None:
    for adapter in (WindowsAdapter(which=lambda n: None), LinuxAdapter(which=lambda n: None, environ={})):
        reg = _registry(adapter)
        names = {t.name for t in reg.all()}
        assert not any(n.startswith("mac_") for n in names), adapter.id
        assert "memory_search" in names  # tools without requirements are untouched
        spec = reg.get("mac_click")
        assert spec is not None
        why = reg.unavailable_reason(spec)
        assert why is not None and "Device Agent" in why


async def test_executor_answers_unavailable_before_any_approval(tmp_path: Path) -> None:
    perms = load_permissions()
    reg = _registry(WindowsAdapter(which=lambda n: None))
    ex = ToolExecutor(reg, perms, KillSwitch(tmp_path), ApprovalPolicy(tmp_path / "approval-mode.json"))
    ctx = ToolContext(principal=Principal(id="desktop", role="owner", channel="desktop"))
    res = await ex.execute("mac_type_text", {"app": "Rechner", "text": "1+1"}, ctx)
    assert res.status == "denied"
    assert "auf diesem System nicht verfügbar" in (res.message or "") and "Device Agent" in (res.message or "")


def test_system_prompt_note_only_off_the_mac_or_with_device_agents() -> None:
    from jarvis.agent.persona import host_note

    assert host_note("mac", "macOS", False) == ""  # the Mac prompt stays word for word
    assert host_note(None) == ""
    win = host_note("windows", "Windows", False)
    assert "Windows" in win and "`mac_*`-Werkzeuge gibt es hier nicht" in win and "device_" not in win
    both = host_note("mac", "macOS", True)
    assert both.startswith("\n\n## Weitere Rechner") and "`device_*`" in both
