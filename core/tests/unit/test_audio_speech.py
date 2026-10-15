import numpy as np

from jarvis.voice.audio import EnergyVAD, resample, ulaw_decode, ulaw_encode
from jarvis.voice.speech import _clean_transcript, speakable, split_sentences


def test_ulaw_roundtrip_is_close() -> None:
    t = np.linspace(0, 1, 8000, endpoint=False)
    pcm = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    back = ulaw_decode(ulaw_encode(pcm))
    err = np.abs(back.astype(int) - pcm.astype(int))
    assert err.max() < 0.05 * 12000  # G.711 quantisation error stays small


def test_resample_lengths() -> None:
    pcm = np.zeros(8000, dtype=np.int16)
    assert len(resample(pcm, 8000, 16000)) == 16000
    assert len(resample(np.zeros(24000, dtype=np.int16), 24000, 8000)) == 8000


def test_vad_detects_one_utterance() -> None:
    rate = 16000
    rng = np.random.default_rng(0)
    silence = (rng.standard_normal(rate) * 50).astype(np.int16)
    speech = (np.sin(np.linspace(0, 400 * np.pi, rate)) * 8000).astype(np.int16)
    vad = EnergyVAD(rate, silence_ms=500)
    out = vad.feed(np.concatenate([silence, speech, silence]))
    assert len(out) == 1
    assert 0.9 * rate <= len(out[0]) <= 1.6 * rate


def test_sentence_split_and_speakable() -> None:
    done, rest = split_sentences("Guten Tag, Sir. Die Rechnung ist fertig! Noch etw")
    assert done == ["Guten Tag, Sir.", "Die Rechnung ist fertig!"]
    assert rest == "Noch etw"
    assert speakable("**Fett** und [Link](https://x.de) `code`") == "Fett und Link code"


def test_whisper_hallucinations_removed() -> None:
    assert _clean_transcript("[BLANK_AUDIO]") == ""
    assert _clean_transcript(" Untertitel im Auftrag des ZDF") == ""
    assert _clean_transcript("Hallo Jarvis") == "Hallo Jarvis"
