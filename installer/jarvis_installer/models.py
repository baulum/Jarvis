"""Model downloads: pinned URLs, sizes and checksums, resumable, verified before use.

Groups: ``tts`` (Piper voice for the local Jarvis voice on Windows/Linux), ``stt`` (Whisper large-v3-turbo, best
quality, 574 MB), ``stt-small`` (Whisper small, 190 MB, for weaker PCs), ``wakeword`` ("Hey Jarvis").
A file counts as present only when its checksum matches; a partial download (``.part``) is resumed.
Disk space is checked first – the Mac in particular has little room left.
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

HF = "https://huggingface.co"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ModelFile:
    id: str
    group: str
    label: str
    url: str
    size: int
    dest: str  # relative to the models directory
    sha256: str | None = None
    git_sha1: str | None = None  # Hugging Face non-LFS files publish the git blob id


CATALOG: tuple[ModelFile, ...] = (
    ModelFile(
        "piper-thorsten-high",
        "tts",
        "Piper-Stimme de_DE-thorsten-high",
        f"{HF}/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx",
        113_895_201,
        "piper/de_DE-thorsten-high.onnx",
        sha256="9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1",
    ),
    ModelFile(
        "piper-thorsten-high-config",
        "tts",
        "Piper-Stimme (Konfiguration)",
        f"{HF}/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json",
        4_875,
        "piper/de_DE-thorsten-high.onnx.json",
        git_sha1="2329bd2dfa0c3d7aac10f25fb8fabf754a01ffcf",
    ),
    ModelFile(
        "whisper-large-v3-turbo-q5",
        "stt",
        "Whisper large-v3-turbo (q5_0)",
        f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        574_041_195,
        "ggml-large-v3-turbo-q5_0.bin",
        sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
    ModelFile(
        "whisper-small-q5",
        "stt-small",
        "Whisper small (q5_1)",
        f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
        190_085_487,
        "ggml-small-q5_1.bin",
        sha256="ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    ),
    ModelFile(
        "oww-melspectrogram",
        "wakeword",
        "Wake-Word: Melspektrogramm",
        f"{OWW}/melspectrogram.onnx",
        1_087_958,
        "melspectrogram.onnx",
        sha256="ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    ),
    ModelFile(
        "oww-embedding",
        "wakeword",
        "Wake-Word: Embedding",
        f"{OWW}/embedding_model.onnx",
        1_326_578,
        "embedding_model.onnx",
        sha256="70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
    ),
    ModelFile(
        "oww-hey-jarvis",
        "wakeword",
        "Wake-Word „Hey Jarvis“",
        f"{OWW}/hey_jarvis_v0.1.onnx",
        1_271_370,
        "hey_jarvis_v0.1.onnx",
        sha256="94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
    ),
)

GROUPS = ("tts", "stt", "stt-small", "wakeword")


class DownloadError(RuntimeError):
    pass


class Opener(Protocol):
    def __call__(self, request: urllib.request.Request, timeout: float) -> object: ...


def files_for(groups: list[str] | tuple[str, ...]) -> list[ModelFile]:
    unknown = set(groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"Unbekannte Modellgruppe: {', '.join(sorted(unknown))} (bekannt: {', '.join(GROUPS)})")
    return [m for m in CATALOG if m.group in groups]


def digest_ok(path: Path, model: ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size != model.size:
        return False
    if model.sha256:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest() == model.sha256
    if model.git_sha1:
        data = path.read_bytes()
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == model.git_sha1  # noqa: S324 - git object id
    return True


def missing(models_dir: Path, groups: list[str] | tuple[str, ...]) -> list[ModelFile]:
    return [m for m in files_for(groups) if not digest_ok(models_dir / m.dest, m)]


def space_check(models_dir: Path, todo: list[ModelFile], reserve: int = 500_000_000) -> tuple[bool, int, int]:
    """(enough?, bytes needed incl. a reserve, bytes free) on the drive of ``models_dir``."""
    probe = models_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    already = sum(
        (models_dir / (m.dest + ".part")).stat().st_size for m in todo if (models_dir / (m.dest + ".part")).exists()
    )
    needed = sum(m.size for m in todo) - already + reserve
    return free >= needed, needed, free


def download(
    model: ModelFile,
    models_dir: Path,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Downloads (resuming a ``.part`` file), verifies the checksum and moves the file into place."""
    dest = models_dir / model.dest
    if digest_ok(dest, model):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > model.size:
        part.unlink()
        have = 0
    if have < model.size:
        req = urllib.request.Request(model.url, headers={"User-Agent": "jarvis-installer/0.1"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        resp = opener(req, timeout=60)
        status = getattr(resp, "status", 200)
        if have and status != 206:  # server ignored the range: start over
            have = 0
        mode = "ab" if have else "wb"
        with part.open(mode) as f:
            done = have
            while True:
                block = resp.read(CHUNK)  # type: ignore[attr-defined]
                if not block:
                    break
                f.write(block)
                done += len(block)
                if progress:
                    progress(done, model.size)
        close = getattr(resp, "close", None)
        if close:
            close()
    if not digest_ok(part, model):
        part.unlink(missing_ok=True)
        raise DownloadError(f"{model.label}: Prüfsumme stimmt nicht – Datei verworfen, bitte erneut versuchen")
    part.replace(dest)
    return dest
