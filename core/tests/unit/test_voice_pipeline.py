"""Streaming speech: chunking, commitment guard, ordered hybrid synthesis, socket fallback, barge-in cancel."""

import asyncio

import pytest

from jarvis.voice.pipeline import CommitmentGuard, SpeechTurn, TextChunker, claims_action


def test_chunker_cuts_first_clause_early_and_respects_ordinals() -> None:
    c = TextChunker()
    out = c.feed("Sehr gerne, Sir, der Termin am 15. Oktober um 9 Uhr ist bestätigt")
    assert out == ["Sehr gerne, Sir,"]  # first piece at a clause boundary, long before the sentence ends
    out = c.feed(". Danach z. B. die Rechnung. ")
    assert out == ["der Termin am 15. Oktober um 9 Uhr ist bestätigt.", "Danach z. B. die Rechnung."]
    assert c.feed("Und noch etwas") == [] and c.finish() == ["Und noch etwas"]


def test_chunker_short_first_sentence() -> None:
    c = TextChunker()
    assert c.feed("Heute ist Freitag, Sir. Mor") == ["Heute ist Freitag, Sir."]
    assert c.feed("gen ist Samstag.\n") == ["Morgen ist Samstag."]


@pytest.mark.parametrize(
    "s,claim",
    [
        ("Sehr wohl, Sir. Ich kümmere mich darum.", True),
        ("Die Rechnung ist angelegt.", True),
        ("Ich lege den Kunden jetzt an.", True),
        ("Das läuft jetzt im Hintergrund.", True),
        ("Heute ist Freitag.", False),
        ("Soll ich die Rechnung anlegen?", False),
    ],
)
def test_claims_action(s: str, claim: bool) -> None:
    assert claims_action(s) is claim


def test_guard_holds_claims_until_tool_success() -> None:
    g = CommitmentGuard()
    assert g.admit("Sehr wohl, Sir.") == ["Sehr wohl, Sir."]
    assert g.admit("Ich kümmere mich darum.") == []
    assert g.admit("Das dauert etwa zehn Minuten.") == []  # stays behind the held claim (order)
    assert g.on_tool(False) == []
    assert g.on_tool(True) == ["Ich kümmere mich darum.", "Das dauert etwa zehn Minuten."]
    assert g.admit("Die Rechnung ist angelegt.") == ["Die Rechnung ist angelegt."]


def test_guard_drops_unbacked_claims() -> None:
    g = CommitmentGuard()
    g.admit("Ich kümmere mich darum.")
    g.admit("Bis gleich.")
    assert g.finish() == ["Bis gleich."] and g.dropped == ["Ich kümmere mich darum."]


class FakeWs:
    def __init__(self, fail_open: bool = False, fail_stream: bool = False) -> None:
        self.fail_open, self.fail_stream = fail_open, fail_stream
        self.sent: list[str] = []
        self.q: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def open(self) -> None:
        await asyncio.sleep(0.02)
        if self.fail_open:
            raise RuntimeError("402")

    async def send_text(self, t: str) -> None:
        self.sent.append(t.strip())

    async def flush(self) -> None:
        await self.q.put(f"WS:{self.sent[-1]}|".encode())

    async def stop(self) -> None:
        await self.q.put(None)

    async def audio(self):
        if self.fail_stream:
            raise RuntimeError("stream broke")
        while (x := await self.q.get()) is not None:
            yield x

    async def close(self) -> None:
        self.closed = True


class FakeTTS:
    def __init__(self, ws: FakeWs | None = None, delays: dict[str, float] | None = None) -> None:
        self.ws, self.delays = ws, delays or {}
        self.http: list[str] = []
        self.voices: list[str | None] = []
        self.warmed = False
        self.voice = "jarvis"

    async def warm(self, voice: str | None = None) -> None:
        self.warmed = True

    async def live_session(self, rate: int, voice: str | None = None):
        return self.ws

    async def stream(self, text: str, rate: int, voice: str | None = None):
        self.http.append(text)
        self.voices.append(voice)
        await asyncio.sleep(self.delays.get(text, 0.001))
        yield f"H:{text}|".encode()


async def _run(turn: SpeechTurn, deltas: list[str]) -> bytes:
    await turn.prepare()
    for d in deltas:
        turn.text(d)
        await asyncio.sleep(0)
    await turn.finish()
    return b""


async def test_http_transport_keeps_order_with_prefetch() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    tts = FakeTTS(delays={"Erster Satz.": 0.05, "Zweiter Satz.": 0.001})
    turn = SpeechTurn(tts, sink, transport="http")  # type: ignore[arg-type]
    await _run(turn, ["Erster Satz. ", "Zweiter Satz. "])
    assert b"".join(out) == b"H:Erster Satz.|H:Zweiter Satz.|"
    assert tts.warmed and turn.metrics["first_audio_ms"] >= turn.metrics["first_piece_ms"]


async def test_hybrid_first_piece_http_rest_websocket() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    ws = FakeWs()
    tts = FakeTTS(ws)
    turn = SpeechTurn(tts, sink, transport="hybrid")  # type: ignore[arg-type]
    await _run(turn, ["Heute ist Freitag, Sir. ", "Morgen ist Samstag. ", "Und dann Sonntag."])
    assert b"".join(out) == b"H:Heute ist Freitag, Sir.|WS:Morgen ist Samstag.|WS:Und dann Sonntag.|"
    assert tts.http == ["Heute ist Freitag, Sir."] and ws.sent == ["Morgen ist Samstag.", "Und dann Sonntag."]


async def test_hybrid_short_reply_closes_unused_socket() -> None:
    ws = FakeWs()

    async def sink(b: bytes) -> None:
        pass

    turn = SpeechTurn(FakeTTS(ws), sink)  # type: ignore[arg-type]
    await _run(turn, ["Jawohl, Sir."])
    assert ws.closed and ws.sent == []


async def test_websocket_failure_falls_back_to_http() -> None:
    for ws in (FakeWs(fail_open=True), FakeWs(fail_stream=True)):
        out: list[bytes] = []

        async def sink(b: bytes, out=out) -> None:
            out.append(b)

        tts = FakeTTS(ws)
        turn = SpeechTurn(tts, sink, transport="websocket")  # type: ignore[arg-type]
        await _run(turn, ["Eins ist gut. ", "Zwei ist besser. "])
        assert b"".join(out) == b"H:Eins ist gut.|H:Zwei ist besser.|", ws.__dict__


async def test_cancel_stops_forwarding_immediately() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    tts = FakeTTS(delays={"Ein sehr langer zweiter Satz.": 0.3})
    turn = SpeechTurn(tts, sink, transport="http")  # type: ignore[arg-type]
    await turn.prepare()
    turn.text("Kurz. Ein sehr langer zweiter Satz. ")
    await asyncio.sleep(0.05)
    await turn.cancel()
    await asyncio.sleep(0.35)
    assert out == [b"H:Kurz.|"]
    turn.text("Noch mehr. ")
    assert turn.segments[-1].text == "Ein sehr langer zweiter Satz."  # nothing new after cancel


async def test_guarded_claim_spoken_only_after_tool() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    turn = SpeechTurn(FakeTTS(), sink, transport="http")  # type: ignore[arg-type]
    await turn.prepare()
    turn.text("Sehr wohl, Sir. Ich kümmere mich darum. ")
    await asyncio.sleep(0.02)
    assert b"".join(out) == b"H:Sehr wohl, Sir.|"
    turn.tool_finished(True)
    m = await turn.finish()
    assert b"".join(out) == "H:Sehr wohl, Sir.|H:Ich kümmere mich darum.|".encode() and m["dropped_claims"] == 0


async def test_tool_start_acknowledges_once_when_nothing_was_said() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    turn = SpeechTurn(FakeTTS(), sink, transport="http")  # type: ignore[arg-type]
    await turn.prepare()
    turn.tool_started()
    turn.tool_started()  # second tool: no second acknowledgement
    turn.tool_finished(True)
    turn.text("Der Termin ist eingetragen. ")
    await turn.finish()
    assert b"".join(out) == b"H:Einen Moment, Sir.|H:Der Termin ist eingetragen.|"


async def test_no_acknowledgement_after_speech_started() -> None:
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    turn = SpeechTurn(FakeTTS(), sink, transport="http")  # type: ignore[arg-type]
    await turn.prepare()
    turn.text("Ich schaue nach. ")
    turn.tool_started()
    await turn.finish()
    assert b"".join(out) == b"H:Ich schaue nach.|"


async def test_a_persona_switch_never_changes_the_voice_in_the_middle_of_a_reply() -> None:
    tts = FakeTTS()
    out: list[bytes] = []

    async def sink(b: bytes) -> None:
        out.append(b)

    turn = SpeechTurn(tts, sink, transport="http")  # type: ignore[arg-type]
    await turn.prepare()
    turn.text("Erster Satz, der schon gesprochen wird. ")
    tts.voice = "rene_redo"  # Sir switches to René Redo while Jarvis is still talking
    turn.text("Zweiter Satz kommt noch. ")
    await turn.finish()
    assert tts.voices == ["jarvis", "jarvis"]
    nxt = SpeechTurn(tts, sink, transport="http", ack_phrase="Moment, Alter!")  # type: ignore[arg-type]
    await nxt.prepare()
    nxt.tool_started()
    await nxt.finish()
    assert tts.voices[-1] == "rene_redo" and tts.http[-1] == "Moment, Alter!"
