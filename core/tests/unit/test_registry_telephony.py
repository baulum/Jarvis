import base64
import hashlib
import hmac
from typing import Any

import pytest

from jarvis.config import load_permissions
from jarvis.telephony.service import digits_from_speech, stream_twiml
from jarvis.tools.registry import ToolContext, ToolRegistry, ToolSpec


async def _noop(ctx: ToolContext, a: dict[str, Any]) -> Any:
    return None


def spec(name: str, risk: str = "read", connector: str = "x") -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object", "properties": {}}, _noop, risk, connector)  # type: ignore[arg-type]


def test_tool_name_validation() -> None:
    with pytest.raises(ValueError):
        spec("Bad.Name")


def test_permission_overrides_only_raise_risk() -> None:
    reg = ToolRegistry(load_permissions())
    for s in (spec("shell_run", "low", "shell"), spec("mac_applescript", "high", "mac"),
              spec("lexware_finalize_invoice", "medium", "lexware")):
        reg.register(s)
    assert reg.effective_risk(reg.get("shell_run")) == "high"  # type: ignore[arg-type]
    assert reg.effective_risk(reg.get("lexware_finalize_invoice")) == "critical"  # type: ignore[arg-type]
    assert not reg.is_enabled(reg.get("mac_applescript"))  # type: ignore[arg-type]


def test_connector_gate_hides_tools() -> None:
    reg = ToolRegistry(load_permissions())
    reg.register(spec("lexware_search_contact", "read", "lexware"))
    reg.set_connector_gate("lexware", lambda: False)
    assert reg.all() == []
    assert len(reg.all(include_disabled=True)) == 1


def test_spoken_pin_digits() -> None:
    assert digits_from_speech("Meine PIN ist eins zwei drei vier") == "1234"
    assert digits_from_speech("4 7 1 1") == "4711"


def test_twiml_escapes_parameters() -> None:
    import uuid
    import xml.etree.ElementTree as ET

    xml = stream_twiml("https://x.example", uuid.uuid4(), 'tok"<>&')
    root = ET.fromstring(xml)  # must be well-formed despite hostile characters
    stream = root.find("./Connect/Stream")
    assert stream is not None and stream.get("url") == "wss://x.example/api/telephony/stream"
    params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
    assert params["token"] == 'tok"<>&'


def test_twilio_signature_algorithm() -> None:
    # Reproduces Twilio's documented scheme: base64(HMAC-SHA1(token, url + sorted(k+v))).
    from jarvis.telephony.service import TwilioProvider

    class FakeState:
        class settings:  # noqa: N801
            class telephony:  # noqa: N801
                class twilio:  # noqa: N801
                    account_sid_secret = "a"
                    auth_token_secret = "b"

    prov = TwilioProvider.__new__(TwilioProvider)
    prov._creds = lambda: ("sid", "12345")  # type: ignore[method-assign]
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234"}
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    sig = base64.b64encode(hmac.new(b"12345", data.encode(), hashlib.sha1).digest()).decode()
    assert prov.validate_signature(url, params, sig)
    assert not prov.validate_signature(url, {**params, "Digits": "9999"}, sig)
    assert not prov.validate_signature(url, params, None)
