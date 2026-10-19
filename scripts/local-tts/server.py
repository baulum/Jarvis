"""Local Jarvis voice: Qwen3-TTS (MLX) cloning the Jarvis reference clip, streamed as raw PCM.

Runs in its own venv (mlx-audio pins its own numpy/transformers), started by scripts/start.sh:
    POST /tts   {"text": "..."}  → chunked 16-bit mono PCM, rate in the X-Sample-Rate header
    GET  /health                 → {"ok": true, "voice": ..., "sample_rate": ...}

Measured on an M1 Pro (16 GB), 0.6B-8bit with a 6 s reference: ~0.7–0.9 s to first audio, realtime
factor ~0.75. A longer reference sounds no better but makes every step slower (19 s → RTF ~1.1).
Generation is serialised (one GPU); a client that disconnects (barge-in) stops its generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from mlx_audio.tts.utils import load_model

logger = logging.getLogger("local-tts")


class Voice:
    def __init__(self, repo: str, ref: Path, language: str, streaming_interval: float) -> None:
        self.repo = repo
        self.model = load_model(repo)
        self.sample_rate = int(self.model.sample_rate)
        self.ref_audio = str(ref.with_suffix(".wav"))
        self.ref_text = ref.with_suffix(".txt").read_text(encoding="utf-8").strip()
        digest = hashlib.sha256(ref.with_suffix(".wav").read_bytes()).hexdigest()[:12]
        self.id = f"{repo}:{ref.name}:{digest}"  # the core keys its phrase cache on this
        self.language = language
        self.streaming_interval = streaming_interval
        self.lock = threading.Lock()
        list(self.chunks("Hallo."))  # warm-up: compiles kernels and caches the encoded reference

    def chunks(self, text: str):
        for r in self.model.generate(
            text=text,
            ref_audio=self.ref_audio,
            ref_text=self.ref_text,
            lang_code=self.language,
            stream=True,
            streaming_interval=self.streaming_interval,
            verbose=False,
        ):
            audio = np.asarray(r.audio, dtype=np.float32).reshape(-1)
            yield (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def make_handler(voice: Voice) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            logger.debug(fmt, *args)

        def _json(self, code: int, body: dict) -> None:
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
                        if first is None:
                            first = time.perf_counter() - t0
                        self.wfile.write(f"{len(pcm):X}\r\n".encode() + pcm + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    logger.info("client gone, generation stopped: %r", text[:40])
                    return
            logger.info("%d chars, first audio %.2f s, total %.2f s", len(text), first or 0, time.perf_counter() - t0)

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8179)
    p.add_argument("--model", default="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    p.add_argument("--ref", type=Path, required=True, help="reference clip without suffix (<ref>.wav + <ref>.txt)")
    p.add_argument("--language", default="de")
    p.add_argument("--streaming-interval", type=float, default=0.32)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    voice = Voice(a.model, a.ref, a.language, a.streaming_interval)
    logger.info("ready on http://%s:%d (%s, %d Hz)", a.host, a.port, a.model, voice.sample_rate)
    ThreadingHTTPServer((a.host, a.port), make_handler(voice)).serve_forever()


if __name__ == "__main__":
    main()
