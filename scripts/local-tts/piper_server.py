"""Local Jarvis voice on Windows/Linux: Piper (ONNX, CPU) streamed as raw PCM – same HTTP contract as server.py.

The Mac uses server.py (Qwen3-TTS on MLX, Apple Silicon only). Everywhere else this server answers on the same
port, so ``voice.tts.provider: local`` works unchanged:
    POST /tts   {"text": "..."}  → chunked 16-bit mono PCM, rate in the X-Sample-Rate header
    GET  /health                 → {"ok": true, "voice": ..., "sample_rate": ...}

The model (default de_DE-thorsten-high, ~110 MB) is downloaded and verified by the installer
(``installer/jarvis_install.py``) into ``<data_dir>/models/piper``. Runs in its own venv
(``pip install -r requirements-piper.txt``). Synthesis is serialised; a client that disconnects (barge-in)
stops its synthesis between sentences.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("piper-tts")
_SENTENCE = re.compile(r"(?<=[.!?…:;])\s+")


class Voice:
    def __init__(self, model: Path, length_scale: float) -> None:
        from piper import PiperVoice

        if not model.is_file() or not model.with_suffix(model.suffix + ".json").is_file():
            raise SystemExit(f"Piper-Modell fehlt: {model} (+ .json) – Installer ausführen")
        self.voice: Any = PiperVoice.load(str(model))
        self.sample_rate = int(self.voice.config.sample_rate)
        self.length_scale = length_scale
        digest = hashlib.sha256(model.with_suffix(model.suffix + ".json").read_bytes()).hexdigest()[:12]
        self.id = f"piper:{model.stem}:{digest}"  # the core keys its phrase cache on this
        self.lock = threading.Lock()
        list(self.chunks("Hallo."))  # warm-up: loads the ONNX session

    def chunks(self, text: str) -> Iterator[bytes]:
        """One PCM block per sentence (first audio after the first sentence, not after the whole answer)."""
        for sentence in [s for s in _SENTENCE.split(text) if s.strip()]:
            yield from self._synthesize(sentence)

    def _synthesize(self, text: str) -> Iterator[bytes]:
        if hasattr(self.voice, "synthesize_stream_raw"):  # piper-tts 1.2
            yield from self.voice.synthesize_stream_raw(text, length_scale=self.length_scale)
            return
        from piper import SynthesisConfig  # piper-tts ≥ 1.3

        for chunk in self.voice.synthesize(text, syn_config=SynthesisConfig(length_scale=self.length_scale)):
            yield chunk.audio_int16_bytes


def make_handler(voice: Voice) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            logger.debug(fmt, *args)

        def _json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("", "/health"):
                self._json(200, {"ok": True, "voice": voice.id, "sample_rate": voice.sample_rate})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/tts":
                self._json(404, {"error": "not found"})
                return
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                text = str(req.get("text") or "").strip()
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid json"})
                return
            if not text:
                self._json(400, {"error": "text missing"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16")
            self.send_header("X-Sample-Rate", str(voice.sample_rate))
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            t0 = time.perf_counter()
            first = None
            with voice.lock:
                try:
                    for pcm in voice.chunks(text):
                        if not pcm:
                            continue
                        if first is None:
                            first = time.perf_counter() - t0
                        self.wfile.write(f"{len(pcm):X}\r\n".encode() + pcm + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    logger.info("client gone, synthesis stopped: %r", text[:40])
                    return
            logger.info("%d chars, first audio %.2f s, total %.2f s", len(text), first or 0, time.perf_counter() - t0)

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8179)
    p.add_argument("--model", type=Path, required=True, help="Piper .onnx model (the .onnx.json must lie next to it)")
    p.add_argument("--length-scale", type=float, default=0.95, help="< 1 speaks faster, > 1 slower")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    voice = Voice(a.model, a.length_scale)
    logger.info("ready on http://%s:%d (%s, %d Hz)", a.host, a.port, a.model.name, voice.sample_rate)
    ThreadingHTTPServer((a.host, a.port), make_handler(voice)).serve_forever()


if __name__ == "__main__":
    main()
