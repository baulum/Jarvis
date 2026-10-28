"""What ``system_status`` reports for the Mac voice daemon."""

from types import SimpleNamespace

from jarvis.app_state import AppState


def health(**voice_state: object) -> str:
    return AppState.voice_daemon_health(SimpleNamespace(voice_state=voice_state))  # type: ignore[arg-type]


def test_connected_and_listening_is_ok() -> None:
    assert health(connected=True, state="idle", muted=False) == "ok"


def test_muted_daemon_is_not_reported_as_ok() -> None:
    # 2026-10-28: muted since 09:11 (microphone and speaker closed), yet system_status said "ok" – the next look at the
    # status has to show why Jarvis neither hears nor speaks on the Mac
    assert health(connected=True, state="muted", muted=True) == "muted"


def test_daemon_error_and_offline() -> None:
    assert health(connected=True, state="error", muted=False, detail="Mikrofon nicht verfügbar") == "error"
    assert health(connected=False) == "offline"
    assert health() == "offline"
