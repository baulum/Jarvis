"""Telephony: inbound/outbound calls with bidirectional audio streaming.

Provider abstraction: ``TelephonyProvider`` (place calls, validate webhooks, render the
answer document). Implemented: Twilio (Programmable Voice + Media Streams; SIP endpoints
can be routed to the same webhook via a Twilio SIP Domain / Elastic SIP Trunk) and a
clearly labelled LOCAL TEST MODE that speaks the identical media-stream protocol over a
local WebSocket (no real phone call, for development without a phone number).

Security:
* webhooks are verified with the X-Twilio-Signature (HMAC-SHA1) — unsigned requests are rejected;
* the media WebSocket requires a one-time stream token bound to the call;
* the caller ID is NEVER sufficient: the owner must enter the PIN (DTMF or spoken digits);
  3 failures end the call. Unverified callers get no tools at all;
* per-number and global rate limits, full call log with transcript in Postgres.
"""

from __future__ import annotations

import abc
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from jarvis.db import session_scope
from jarvis.db.models import Call
from jarvis.events import bus
from jarvis.secrets import secret_store
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.security.guards import rate_limiter
from jarvis.voice.audio import EnergyVAD, resample, rms, ulaw_decode, ulaw_encode
from jarvis.voice.commands import is_end_conversation
from jarvis.voice.speech import speakable

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
RATE = 8000
FRAME_BYTES = 160  # 20 ms of µ-law @ 8 kHz
_WORD_DIGITS = {
    "null": "0",
    "eins": "1",
    "ein": "1",
    "zwei": "2",
    "zwo": "2",
    "drei": "3",
    "vier": "4",
    "fünf": "5",
    "fuenf": "5",
    "sechs": "6",
    "sieben": "7",
    "acht": "8",
    "neun": "9",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def digits_from_speech(text: str) -> str:
    out = []
    for token in re.findall(r"\d+|[a-zäöüß]+", text.lower()):
        if token.isdigit():
            out.append(token)
        elif token in _WORD_DIGITS:
            out.append(_WORD_DIGITS[token])
    return "".join(out)


class TelephonyProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    def configured(self) -> bool: ...

    @abc.abstractmethod
    async def place_call(self, to: str, call_id: uuid.UUID, stream_token: str) -> str:
        """Start an outbound call; returns the provider call id."""


class TwilioProvider(TelephonyProvider):
    name = "twilio"

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.telephony

    def _creds(self) -> tuple[str, str]:
        sid = secret_store.get(self.cfg.twilio.account_sid_secret)
        token = secret_store.get(self.cfg.twilio.auth_token_secret)
        if not sid or not token:
            raise RuntimeError("Twilio-Zugangsdaten fehlen")
        return sid, token

    def configured(self) -> bool:
        return bool(
            secret_store.get(self.cfg.twilio.account_sid_secret)
            and secret_store.get(self.cfg.twilio.auth_token_secret)
            and self.cfg.twilio.from_number
            and self.state.settings.server.public_base_url
        )

    def missing(self) -> list[str]:
        out = []
        if not secret_store.get(self.cfg.twilio.account_sid_secret):
            out.append(f"Keychain: {self.cfg.twilio.account_sid_secret}")
        if not secret_store.get(self.cfg.twilio.auth_token_secret):
            out.append(f"Keychain: {self.cfg.twilio.auth_token_secret}")
        if not self.cfg.twilio.from_number:
            out.append("telephony.twilio.from_number (Twilio-Rufnummer)")
        if not self.state.settings.server.public_base_url:
            out.append("server.public_base_url (öffentliche HTTPS-URL, z. B. via Cloudflare Tunnel)")
        if not secret_store.get(self.cfg.pin_secret):
            out.append(f"Keychain: {self.cfg.pin_secret} (Telefon-PIN)")
        return out

    def validate_signature(self, url: str, params: dict[str, str], signature: str | None) -> bool:
        if not signature:
            return False
        _, token = self._creds()
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        digest = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
        return hmac.compare_digest(digest, signature)

    async def place_call(self, to: str, call_id: uuid.UUID, stream_token: str) -> str:
        sid, token = self._creds()
        base = self.state.settings.server.public_base_url.rstrip("/")
        twiml = stream_twiml(base, call_id, stream_token)
        async with httpx.AsyncClient(timeout=20, auth=(sid, token)) as c:
            r = await c.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                data={
                    "To": to,
                    "From": self.cfg.twilio.from_number,
                    "Twiml": twiml,
                    "StatusCallback": f"{base}/api/telephony/twilio/status",
                    "StatusCallbackEvent": "completed",
                },
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Twilio-Fehler {r.status_code}: {r.text[:200]}")
        return str(r.json()["sid"])


def stream_twiml(base_url: str, call_id: uuid.UUID, stream_token: str) -> str:
    ws = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/telephony/stream"
    return (
        "<Response><Connect><Stream url=" + quoteattr(ws) + ">"
        f'<Parameter name="call_id" value="{call_id}"/>'
        f'<Parameter name="token" value={quoteattr(stream_token)}/>'
        "</Stream></Connect></Response>"
    )


def reject_twiml(message: str) -> str:
    return f'<Response><Say language="de-DE">{escape(speakable(message))}</Say><Hangup/></Response>'


@dataclass
class CallState:
    call_id: uuid.UUID
    direction: str
    from_number: str
    to_number: str
    purpose: str = ""
    authenticated: bool = False
    pin_attempts: int = 0
    dtmf: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)
    session_id: uuid.UUID | None = None
    speaking: bool = False
    speak_task: asyncio.Task[None] | None = None
    stream_sid: str = ""
    local_test: bool = False


class TelephonyService:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.telephony
        self.twilio = TwilioProvider(state)
        self._stream_tokens: dict[str, tuple[uuid.UUID, float]] = {}
        self.active: dict[uuid.UUID, CallState] = {}

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "provider": self.cfg.provider,
            "twilio_configured": self.twilio.configured(),
            "missing": self.twilio.missing(),
            "pin_configured": bool(secret_store.get(self.cfg.pin_secret)),
            "active_calls": [
                {
                    "call_id": str(c.call_id),
                    "direction": c.direction,
                    "authenticated": c.authenticated,
                    "local_test": c.local_test,
                }
                for c in self.active.values()
            ],
        }

    def issue_stream_token(self, call_id: uuid.UUID) -> str:
        token = secrets.token_urlsafe(24)
        self._stream_tokens[token] = (call_id, time.time() + 120)
        return token

    def consume_stream_token(self, token: str, call_id: str) -> uuid.UUID | None:
        entry = self._stream_tokens.pop(token, None)
        if entry is None or entry[1] < time.time() or str(entry[0]) != call_id:
            return None
        return entry[0]

    # ------------------------------------------------------------------ inbound webhook
    async def handle_inbound(self, url: str, params: dict[str, str], signature: str | None) -> str:
        if not self.twilio.configured():
            return reject_twiml("Dieser Anschluss ist noch nicht eingerichtet.")
        if not self.twilio.validate_signature(url, params, signature):
            await audit(f"phone:{params.get('From', '?')}", "telephony.bad_signature", outcome="denied")
            raise PermissionError("invalid signature")
        caller = params.get("From", "")
        known = caller in self.cfg.owner_numbers
        if self.cfg.reject_unknown_callers and not known:
            await audit(f"phone:{caller}", "telephony.unknown_caller", outcome="denied")
            return reject_twiml("Dieser Anschluss nimmt keine Anrufe von unbekannten Nummern entgegen.")
        # strangers share one hourly budget of their own, so they can never lock Sir out of his own line
        if not rate_limiter.check(f"call:{caller}", 3, 600) or (
            not known and not rate_limiter.check("calls:global", self.cfg.max_calls_per_hour, 3600)
        ):
            await audit(f"phone:{caller}", "telephony.rate_limited", outcome="denied")
            return reject_twiml("Zu viele Anrufe. Bitte später erneut versuchen.")
        async with session_scope() as s:
            call = Call(
                provider="twilio",
                provider_call_id=params.get("CallSid"),
                direction="inbound",
                from_number=caller,
                to_number=params.get("To", ""),
                status="ringing",
            )
            s.add(call)
            await s.flush()
            call_id = call.id
        await audit(f"phone:{caller}", "telephony.inbound", target=str(call_id))
        await bus.publish("telephony.call", call_id=str(call_id), direction="inbound", status="ringing")
        base = self.state.settings.server.public_base_url.rstrip("/")
        return stream_twiml(base, call_id, self.issue_stream_token(call_id))

    async def handle_status_callback(self, url: str, params: dict[str, str], signature: str | None) -> None:
        if not self.twilio.validate_signature(url, params, signature):
            raise PermissionError("invalid signature")
        async with session_scope() as s:
            from sqlalchemy import select

            call = (
                await s.execute(select(Call).where(Call.provider_call_id == params.get("CallSid")))
            ).scalar_one_or_none()
            if call is not None:
                call.status = params.get("CallStatus", call.status)
                if call.status in ("completed", "failed", "busy", "no-answer", "canceled"):
                    call.ended_at = call.ended_at or datetime.now(UTC)

    # ------------------------------------------------------------------ outbound
    async def call_owner(self, message: str, reason: str = "notification") -> dict[str, Any]:
        if not self.cfg.enabled:
            raise RuntimeError("Telefonie ist deaktiviert")
        if not self.cfg.owner_numbers:
            raise RuntimeError("Keine Besitzer-Rufnummer (telephony.owner_numbers) konfiguriert")
        if self.cfg.provider != "twilio" or not self.twilio.configured():
            raise RuntimeError("Telefonie nicht eingerichtet: " + ", ".join(self.twilio.missing()))
        if not rate_limiter.check("calls:outbound", 6, 3600):
            raise RuntimeError("Rate-Limit für ausgehende Anrufe erreicht")
        to = self.cfg.owner_numbers[0]
        async with session_scope() as s:
            call = Call(
                provider="twilio",
                direction="outbound",
                from_number=self.cfg.twilio.from_number,
                to_number=to,
                status="initiated",
                purpose=message[:1000],
            )
            s.add(call)
            await s.flush()
            call_id = call.id
        provider_id = await self.twilio.place_call(to, call_id, self.issue_stream_token(call_id))
        async with session_scope() as s:
            c = await s.get(Call, call_id)
            if c is not None:
                c.provider_call_id = provider_id
        await audit("telephony", "telephony.outbound", target=str(call_id), details={"reason": reason})
        await bus.publish("telephony.call", call_id=str(call_id), direction="outbound", status="initiated")
        return {"call_id": str(call_id), "provider_call_id": provider_id}

    async def start_local_test_call(self, purpose: str = "") -> tuple[uuid.UUID, str]:
        """LOCAL TEST MODE: register a simulated call (no telephone network involved)."""
        async with session_scope() as s:
            call = Call(
                provider="local_test",
                direction="inbound",
                from_number="local-test",
                to_number="jarvis",
                status="ringing",
                purpose=purpose or "Lokaler Testanruf",
            )
            s.add(call)
            await s.flush()
            call_id = call.id
        return call_id, self.issue_stream_token(call_id)

    # ------------------------------------------------------------------ media stream
    async def media_stream(self, ws: WebSocket) -> None:
        await ws.accept()
        state: CallState | None = None
        vad = EnergyVAD(
            RATE,
            silence_ms=self.state.settings.voice.end_of_turn_silence_ms,
            max_utterance_s=self.state.settings.voice.max_utterance_s,
        )
        utter_q: asyncio.Queue[np.ndarray] = asyncio.Queue()
        worker: asyncio.Task[None] | None = None
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                ev = msg.get("event")
                if ev == "start":
                    start = msg.get("start", {})
                    custom = start.get("customParameters", {})
                    call_id = self.consume_stream_token(custom.get("token", ""), custom.get("call_id", ""))
                    if call_id is None:
                        await audit("telephony", "telephony.stream_rejected", outcome="denied")
                        await ws.close(code=4403)
                        return
                    async with session_scope() as s:
                        call = await s.get(Call, call_id)
                        assert call is not None
                        call.status = "in-progress"
                        state = CallState(
                            call_id,
                            call.direction,
                            call.from_number,
                            call.to_number,
                            call.purpose,
                            stream_sid=msg.get("streamSid") or start.get("streamSid", ""),
                            local_test=call.provider == "local_test",
                        )
                    self.active[call_id] = state
                    await bus.publish(
                        "telephony.call", call_id=str(call_id), status="in-progress", local_test=state.local_test
                    )
                    worker = asyncio.create_task(self._conversation(ws, state, utter_q))
                elif ev == "media" and state is not None:
                    pcm = ulaw_decode(base64.b64decode(msg["media"]["payload"]))
                    if state.speaking and rms(pcm) > 1200:  # barge-in: caller talks over Jarvis
                        await self._stop_speaking(ws, state)
                    for utt in vad.feed(pcm):
                        await utter_q.put(utt)
                elif ev == "dtmf" and state is not None:
                    state.dtmf += str(msg.get("dtmf", {}).get("digit", ""))
                    if state.dtmf.endswith("#") or len(state.dtmf) >= 8:
                        await utter_q.put(np.zeros(0, dtype=np.int16))  # wake the conversation loop
                elif ev == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if worker:
                worker.cancel()
            if state is not None:
                self.active.pop(state.call_id, None)
                async with session_scope() as s:
                    call = await s.get(Call, state.call_id)
                    if call is not None:
                        call.status = "completed"
                        call.ended_at = datetime.now(UTC)
                        call.authenticated = state.authenticated
                        call.transcript = state.transcript
                        call.session_id = state.session_id
                await audit(
                    "telephony",
                    "telephony.ended",
                    target=str(state.call_id),
                    details={"authenticated": state.authenticated, "turns": len(state.transcript)},
                )
                await bus.publish("telephony.call", call_id=str(state.call_id), status="completed")

    async def _conversation(self, ws: WebSocket, st: CallState, q: asyncio.Queue[np.ndarray]) -> None:
        pin = secret_store.get(self.cfg.pin_secret)
        if st.local_test:
            await self._say(ws, st, "Lokaler Testmodus. Dies ist kein echtes Telefonat.")
        if not pin:
            if st.direction == "outbound" and st.purpose:  # nothing to verify against: just deliver the message
                await self._say(ws, st, f"Guten Tag, Sir, hier ist Jarvis. {st.purpose}")
            else:
                await self._say(ws, st, "Hier ist Jarvis.")
            await self._say(
                ws,
                st,
                "Es ist keine Telefon-PIN eingerichtet. Ich kann daher keine Aufträge annehmen. Auf Wiederhören.",
            )
            await self._hangup(ws, st)
            return
        # Whoever picks up hears no details before the PIN was verified.
        await self._say(ws, st, "Hier ist Jarvis.")
        await self._say(
            ws, st, "Bitte nennen Sie Ihre PIN oder geben Sie sie über die Tastatur ein, gefolgt von Raute."
        )
        while True:
            utt = await q.get()
            text = ""
            if utt.size:
                text = await self.state.stt.transcribe_pcm(resample(utt, RATE, 16000), 16000)
                if text and st.authenticated:  # a spoken PIN must never reach the log, the database or the event bus
                    st.transcript.append({"role": "caller", "text": text, "ts": datetime.now(UTC).isoformat()})
                    await bus.publish("telephony.transcript", call_id=str(st.call_id), role="caller", text=text)
            if not st.authenticated:
                entered = (
                    st.dtmf.rstrip("#") if st.dtmf.endswith("#") or len(st.dtmf) >= 8 else digits_from_speech(text)
                )
                if not entered:
                    continue
                st.dtmf = ""
                if secrets.compare_digest(entered, pin):
                    st.authenticated = True
                    await audit(f"phone:{st.from_number}", "telephony.authenticated", target=str(st.call_id))
                    if st.direction == "outbound" and st.purpose:
                        await self._say(ws, st, st.purpose)
                    await self._say(ws, st, "Danke, Sir. Was kann ich für Sie tun?")
                else:
                    st.pin_attempts += 1
                    await audit(
                        f"phone:{st.from_number}",
                        "telephony.pin_failed",
                        target=str(st.call_id),
                        outcome="denied",
                        details={"attempt": st.pin_attempts},
                    )
                    if st.pin_attempts >= 3:
                        await self._say(ws, st, "Die PIN war dreimal falsch. Ich beende das Gespräch.")
                        await self._hangup(ws, st)
                        return
                    await self._say(ws, st, "Die PIN ist nicht korrekt. Bitte erneut.")
                continue
            if not text:
                continue
            if is_end_conversation(text):
                await self._say(ws, st, "Sehr wohl, Sir. Auf Wiederhören.")
                await self._hangup(ws, st)
                return
            principal = Principal(id=st.from_number or "phone", role="phone_owner", channel="phone")
            try:
                result = await self.state.orchestrator.handle(principal, "phone", text, session_id=st.session_id)
            except Exception:
                logger.exception("Phone turn failed")
                await self._say(ws, st, "Das hat leider nicht funktioniert, Sir.")
                continue
            st.session_id = result.session_id
            st.transcript.append({"role": "jarvis", "text": result.text, "ts": datetime.now(UTC).isoformat()})
            await bus.publish("telephony.transcript", call_id=str(st.call_id), role="jarvis", text=result.text)
            await self._say(ws, st, result.text)

    async def _say(self, ws: WebSocket, st: CallState, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        st.speaking = True
        try:
            pcm_bytes = await self.state.tts.synthesize(text, RATE)
            payload = ulaw_encode(np.frombuffer(pcm_bytes, dtype=np.int16))
            for i in range(0, len(payload), FRAME_BYTES * 10):  # 200 ms per message
                if not st.speaking:
                    break
                chunk = payload[i : i + FRAME_BYTES * 10]
                await ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": st.stream_sid,
                            "media": {"payload": base64.b64encode(chunk).decode()},
                        }
                    )
                )
                await asyncio.sleep(len(chunk) / RATE * 0.9)  # pace roughly in real time
            await ws.send_text(json.dumps({"event": "mark", "streamSid": st.stream_sid, "mark": {"name": "eos"}}))
        finally:
            st.speaking = False

    async def _stop_speaking(self, ws: WebSocket, st: CallState) -> None:
        st.speaking = False
        await ws.send_text(json.dumps({"event": "clear", "streamSid": st.stream_sid}))

    async def _hangup(self, ws: WebSocket, st: CallState) -> None:
        await asyncio.sleep(0.5)
        if st.local_test or self.cfg.provider != "twilio":
            await ws.close()
            return
        async with session_scope() as s:
            call = await s.get(Call, st.call_id)
            provider_id = call.provider_call_id if call else None
        if provider_id:
            sid, token = self.twilio._creds()
            async with httpx.AsyncClient(timeout=10, auth=(sid, token)) as c:
                await c.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{provider_id}.json",
                    data={"Status": "completed"},
                )
        await ws.close()


def public_url_for(request_url: str, public_base: str) -> str:
    """Twilio signs the public URL; rebuild it from the configured public base."""
    from urllib.parse import urlsplit

    parts = urlsplit(request_url)
    query = f"?{parts.query}" if parts.query else ""
    return public_base.rstrip("/") + parts.path + query


__all__ = ["TelephonyService", "digits_from_speech", "public_url_for", "stream_twiml", "urlencode"]
