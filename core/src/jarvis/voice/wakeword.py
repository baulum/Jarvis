"""Local "Hey Jarvis" wake-word detection with the openWakeWord ONNX models.

Re-implements openWakeWord's streaming feature pipeline (melspectrogram → Google speech
embedding → wake-word classifier) on top of onnxruntime only, avoiding the package's
heavy/unsupported dependencies (tflite-runtime, scipy, scikit-learn) on Python 3.13.
Audio never leaves the machine. Pre-trained models: https://github.com/dscripka/openWakeWord
(models licensed CC BY-NC-SA 4.0 — fine for personal, non-commercial use).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import numpy.typing as npt
import onnxruntime as ort

CHUNK = 1280  # 80 ms @ 16 kHz — the native step of the feature pipeline
RATE = 16000


class WakeWordDetector:
    def __init__(
        self,
        models_dir: Path,
        model_name: str = "hey_jarvis_v0.1",
        threshold: float = 0.5,
        patience: int = 1,
        cooldown_s: float = 1.5,
    ) -> None:
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(models_dir / "melspectrogram.onnx"), opts, providers=prov)
        self.emb = ort.InferenceSession(str(models_dir / "embedding_model.onnx"), opts, providers=prov)
        self.ww = ort.InferenceSession(str(models_dir / f"{model_name}.onnx"), opts, providers=prov)
        self.threshold = threshold
        self.patience = patience
        self.cooldown_chunks = int(cooldown_s * RATE / CHUNK)
        self.reset()

    def reset(self) -> None:
        self.raw: deque[int] = deque(maxlen=RATE * 4)
        self.mel_buf = np.ones((76, 32), dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.int16)
        self._hits = 0
        self._cooldown = 0
        self.last_score = 0.0
        # Warm the feature buffer with low-level noise (as openWakeWord does) so the
        # classifier sees 16 valid embeddings from the start.
        noise = (np.random.default_rng(0).standard_normal(RATE * 2) * 30).astype(np.int16)
        self.features = np.zeros((0, 96), dtype=np.float32)
        for i in range(0, len(noise) - CHUNK + 1, CHUNK):
            self._process_chunk(noise[i : i + CHUNK], score=False)

    def _melspec(self, samples: npt.NDArray[np.int16]) -> npt.NDArray[np.float32]:
        out = self.mel.run(None, {"input": samples.astype(np.float32)[None, :]})[0]
        spec: npt.NDArray[np.float32] = np.squeeze(out).astype(np.float32) / 10.0 + 2.0
        return spec.reshape(-1, 32)

    def _process_chunk(self, chunk: npt.NDArray[np.int16], score: bool = True) -> float:
        self.raw.extend(chunk.tolist())
        window = np.array(list(self.raw)[-(CHUNK + 160 * 3) :], dtype=np.int16)
        self.mel_buf = np.vstack([self.mel_buf, self._melspec(window)])[-970:]
        x = self.mel_buf[-76:][None, :, :, None].astype(np.float32)
        emb = self.emb.run(None, {"input_1": x})[0].reshape(1, 96)
        self.features = np.vstack([self.features, emb])[-120:]
        if not score or self.features.shape[0] < 16:
            return 0.0
        feats = self.features[-16:][None, :, :].astype(np.float32)
        return float(self.ww.run(None, {"x.1": feats})[0][0][0])

    def feed(self, pcm: npt.NDArray[np.int16]) -> bool:
        """Feed 16 kHz mono int16 audio of any length; returns True when the wake word fired."""
        data = np.concatenate([self.pending, pcm])
        n = len(data) // CHUNK
        self.pending = data[n * CHUNK :]
        fired = False
        for i in range(n):
            s = self._process_chunk(data[i * CHUNK : (i + 1) * CHUNK])
            self.last_score = s
            if self._cooldown > 0:
                self._cooldown -= 1
                continue
            if s >= self.threshold:
                self._hits += 1
                if self._hits >= self.patience:
                    fired = True
                    self._hits = 0
                    self._cooldown = self.cooldown_chunks
            else:
                self._hits = 0
        return fired
