import ipaddress
import time
from datetime import UTC, datetime

import pytest

from jarvis.connectors.browser import _ip_blocked, host_blocked
from jarvis.jobs.scheduler import check_min_interval
from jarvis.logging_setup import redact
from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.mobile.rtc_proxy import _token_of, valid_room_token
from jarvis.tools.executor import audit_args


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.169.254", "100.64.0.1", "100.100.100.100", "::1", "::ffff:10.0.0.1", "0.0.0.0"])
def test_non_public_addresses_are_blocked(ip: str) -> None:
    assert _ip_blocked(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert not _ip_blocked(ipaddress.ip_address(ip))


async def test_page_requests_to_internal_names_are_blocked() -> None:
    for host in ("localhost", "printer.local", "10.0.0.1", "100.101.102.103", "svc.internal"):
        assert await host_blocked(host)


def test_schedule_minimum_interval() -> None:
    check_min_interval("*/5 * * * *")
    check_min_interval("0 8 * * 1-5")
    for cron in ("* * * * *", "*/2 * * * *", "0,1 * * * *"):
        with pytest.raises(ValueError):
            check_min_interval(cron)


def test_own_tokens_are_redacted_in_logs() -> None:
    text = "tok jdv_" + "a" * 40 + " and jat_abc." + "b" * 30 + " key AIza" + "c" * 35
    out = redact(text)
    assert "jdv_a" not in out and "jat_abc" not in out and "AIza" not in out


def test_typed_text_never_lands_in_the_audit_args() -> None:
    assert audit_args("mac_type_text", {"text": "hunter2", "app": "Safari"}) == {"text": "[7 Zeichen]", "app": "Safari"}
    assert len(audit_args("gmail_send", {"body": "x" * 5000})["body"]) < 1600


def test_gateway_allows_model_picker_and_quota_only_as_expected() -> None:
    assert mobile_path_allowed("GET", "/api/agent/model") and mobile_path_allowed("POST", "/api/agent/model")
    assert mobile_path_allowed("GET", "/api/agent/usage")
    assert not mobile_path_allowed("POST", "/api/agent/usage")
    assert not mobile_path_allowed("GET", "/api/backup") and not mobile_path_allowed("POST", "/api/backup/run")
    assert not mobile_path_allowed("POST", "/api/voice/wakeup")


class _LK:
    def credentials(self) -> tuple[str, str]:
        return "k" * 8, "s" * 40


class _State:
    livekit = _LK()


def test_rtc_proxy_requires_a_token_minted_by_this_core() -> None:
    from livekit import api

    good = api.AccessToken("k" * 8, "s" * 40).with_identity("x").with_grants(api.VideoGrants(room_join=True, room="r")).to_jwt()
    bad = api.AccessToken("k" * 8, "z" * 40).with_identity("x").with_grants(api.VideoGrants(room_join=True, room="r")).to_jwt()
    assert valid_room_token(_State(), good)
    assert not valid_room_token(_State(), bad)
    assert not valid_room_token(_State(), None) and not valid_room_token(_State(), "garbage")
    assert _token_of("Bearer abc", "") == "abc" and _token_of(None, "access_token=xyz&a=1") == "xyz"
    assert _token_of(None, "") is None
    _ = (time, datetime, UTC)
