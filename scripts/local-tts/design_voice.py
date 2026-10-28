"""Design a new local voice from a text description (Qwen3-TTS VoiceDesign, MLX) – no recordings of real people.

Produces the reference clip the local TTS server (server.py) clones at run time: ``<out>.wav`` (24 kHz mono) and
``<out>.txt`` (its transcript). The VoiceDesign model (~3 GB) is only needed once for this; afterwards the voice runs
on the small Base model like the Jarvis voice, so it can be deleted again (``--delete-model``).

Several candidates are generated; with ``--whisper`` each one is transcribed by the local whisper server and the
candidate whose transcript matches the text best (and whose length fits) wins – a clip with slurred or missing words
would be cloned with exactly those flaws. ``--f0`` rejects candidates whose median pitch is outside a range: the
description alone does not reliably fix the register (a "hyped young man" came out at ~350 Hz).

    python -I design_voice.py --out voices/rene_redo --text "…" --instruct "…" --f0 85 190 \
        --whisper http://127.0.0.1:8178

How voices/rene_redo was made (2026-10-28): 8 candidates, seed 300, the text in rene_redo.txt and the description
"Adult man, about 30, deep warm baritone voice with strong chest resonance, low pitch. Lively, upbeat and confident,
talking like a funny, cocky buddy who is hyped about a project: quick tempo, punchy emphasis, a grin in his voice,
lots of energy but relaxed, never shrill or high-pitched. Native German speaker." Candidate 7 was picked by
measurement among those in range: cloned by the Base model it speaks at ~105–130 Hz, clearly above Jarvis' deep voice,
with the widest pitch spread of the candidates (~5 semitones) and the fastest tempo (~5 words/s); first audio ~0.3 s,
RTF ~0.5 – as fast as the Jarvis voice.
A shouted reference ("Alter, was ist denn hier los?!") always came out at 250–370 Hz, whatever the description said.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import shutil
import tempfile
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-zäöüß]+", text.lower()))


def median_f0(audio: np.ndarray, rate: int, fmin: float = 60, fmax: float = 450) -> float:
    """Median pitch of the voiced parts (YIN on 16 kHz)."""
    x = np.interp(np.arange(0, len(audio), rate / 16000), np.arange(len(audio)), audio).astype(np.float64)
    sr, n, hop = 16000, 800, 160
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    found: list[float] = []
    for i in range(0, len(x) - n - tmax, hop):
        if np.sqrt(np.mean(x[i : i + n] ** 2)) < 0.03:
            continue
        d = np.array([np.sum((x[i : i + n] - x[i + t : i + t + n]) ** 2) for t in range(tmax + 1)])
        cm = np.ones_like(d)
        cm[1:] = d[1:] * np.arange(1, tmax + 1) / np.maximum(np.cumsum(d[1:]), 1e-9)
        below = np.where(cm[tmin:] < 0.15)[0]
        if below.size:
            t = tmin + int(below[0])
            while t + 1 <= tmax and cm[t + 1] < cm[t]:
                t += 1
            found.append(sr / t)
    return float(np.median(found)) if found else 0.0


def transcribe(url: str, audio: np.ndarray, rate: int) -> str:
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="WAV", subtype="PCM_16")
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
        + buf.getvalue()
        + b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nde\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'.encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(  # noqa: S310 - the local whisper server given on the command line
        url.rstrip("/") + "/inference",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - local whisper server
        return str(json.loads(r.read()).get("text", "")).strip()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True, help="output path without suffix (<out>.wav + <out>.txt)")
    p.add_argument("--text", required=True, help="what the reference clip says (5–8 s of speech are ideal)")
    p.add_argument("--instruct", required=True, help="description of the voice")
    p.add_argument("--language", default="german")
    p.add_argument("--candidates", type=int, default=4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--min-s", type=float, default=4.5)
    p.add_argument("--max-s", type=float, default=9.0)
    p.add_argument("--whisper", default="", help="URL of a whisper.cpp server to check the candidates")
    p.add_argument("--f0", type=float, nargs=2, metavar=("MIN", "MAX"), help="allowed median pitch in Hz")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--delete-model", action="store_true", help="remove the VoiceDesign model from the HF cache after")
    a = p.parse_args()

    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    model = load_model(a.model)
    rate = int(model.sample_rate)
    best: tuple[float, np.ndarray] | None = None
    a.out.parent.mkdir(parents=True, exist_ok=True)
    drafts = Path(tempfile.mkdtemp(prefix="voice-design-"))  # every candidate, to compare by ear
    print(f"candidates: {drafts}")
    for i in range(a.candidates):
        mx.random.seed(a.seed + i)
        chunks = [
            np.asarray(r.audio, dtype=np.float32).reshape(-1)
            for r in model.generate_voice_design(text=a.text, instruct=a.instruct, language=a.language)
        ]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.89  # same loudness for every candidate (and the final clip)
        seconds = len(audio) / rate
        heard = transcribe(a.whisper, audio, rate) if a.whisper else ""
        match = difflib.SequenceMatcher(None, _words(a.text), _words(heard)).ratio() if a.whisper else 1.0
        f0 = median_f0(audio, rate)
        fits = a.min_s <= seconds <= a.max_s and (not a.f0 or a.f0[0] <= f0 <= a.f0[1])
        score = match + (0.0 if fits else -1.0)
        sf.write(drafts / f"{a.out.name}.candidate{i}.wav", audio, rate, subtype="PCM_16")
        print(f"candidate {i}: {seconds:.1f} s, pitch {f0:.0f} Hz, match {match:.2f}, heard: {heard!r}")
        if best is None or score > best[0]:
            best = (score, audio)
    assert best is not None
    sf.write(a.out.with_suffix(".wav"), best[1], rate, subtype="PCM_16")
    a.out.with_suffix(".txt").write_text(a.text + "\n", encoding="utf-8")
    print(f"→ {a.out.with_suffix('.wav')} (score {best[0]:.2f})")
    if a.delete_model:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id == a.model:
                shutil.rmtree(repo.repo_path, ignore_errors=True)
                print(f"removed {repo.repo_path}")


if __name__ == "__main__":
    main()
