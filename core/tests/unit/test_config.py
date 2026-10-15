from jarvis.config import RISK_ORDER, _apply_env_overrides, load_permissions, load_personality, load_settings


def test_shipped_configs_validate() -> None:
    s = load_settings()
    assert s.server.host == "127.0.0.1"
    p = load_permissions()
    assert "owner" in p.roles and p.roles["voice_owner"].can_approve is False
    assert load_personality().owner_title == "Sir"


def test_env_override_parses_yaml_scalars() -> None:
    data = _apply_env_overrides(
        {"server": {"port": 1}}, {"JARVIS__SERVER__PORT": "9000", "JARVIS__VOICE__ENABLED": "false"}
    )
    assert data["server"]["port"] == 9000
    assert data["voice"]["enabled"] is False


def test_risk_order_is_total() -> None:
    assert sorted(RISK_ORDER, key=RISK_ORDER.get) == ["read", "low", "medium", "high", "critical"]
