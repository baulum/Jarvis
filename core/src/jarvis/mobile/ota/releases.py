"""Released APKs with their manifest: ``<dir>/<versionCode>/manifest.json`` + ``jarvis-mobile-<code>.apk``.

A release only lands here after the build was verified (signature, package, versionCode, checksum) – a defective
build is never stored and so never offered. Withdrawing a release hides it from the phones; the newest release
that is not withdrawn is the one they install. Older releases are removed beyond ``keep``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
SCHEMA = 1
_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(slots=True)
class Release:
    application_id: str
    version_code: int
    version_name: str
    commit: str
    sha256: str
    size: int
    signing_cert_sha256: str
    file: str
    created_at: str
    branch: str = "main"
    min_sdk: int | None = None
    target_sdk: int | None = None
    changelog: list[dict[str, str]] = field(default_factory=list)
    withdrawn: bool = False
    withdrawn_reason: str | None = None
    rebuild_of: int | None = None  # recovery: the release whose code was rebuilt with a new versionCode
    schema: int = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public(self) -> dict[str, Any]:
        """What phones and the desktop see (no local paths)."""
        d = self.to_dict()
        d["download_path"] = f"/api/mobile/app-update/releases/{self.version_code}/apk"
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Release:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


class ReleaseError(RuntimeError):
    pass


class ReleaseStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _rdir(self, code: int) -> Path:
        return self.dir / str(int(code))

    def all(self) -> list[Release]:
        """Every stored release, newest first. Unreadable or incomplete entries are skipped."""
        out: list[Release] = []
        if not self.dir.is_dir():
            return out
        for d in self.dir.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            try:
                rel = Release.from_dict(json.loads((d / MANIFEST).read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if rel.version_code == int(d.name) and (d / rel.file).is_file():
                out.append(rel)
        return sorted(out, key=lambda r: r.version_code, reverse=True)

    def get(self, code: int) -> Release | None:
        return next((r for r in self.all() if r.version_code == code), None)

    def latest(self) -> Release | None:
        """The release phones should run: the newest one that is not withdrawn."""
        return next((r for r in self.all() if not r.withdrawn), None)

    def apk_path(self, rel: Release) -> Path:
        return self._rdir(rel.version_code) / rel.file

    def add(self, apk: Path, rel: Release) -> Release:
        """Copies the verified APK into the store and writes its manifest (the manifest last: a crash in between
        leaves no half release). The checksum is checked again on the copy."""
        target = self._rdir(rel.version_code)
        if target.exists():
            raise ReleaseError(f"Release {rel.version_code} existiert bereits")
        tmp = self.dir / f".incoming-{rel.version_code}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            shutil.copyfile(apk, tmp / rel.file)
            if sha256_file(tmp / rel.file) != rel.sha256:
                raise ReleaseError("Prüfsumme der kopierten APK stimmt nicht")
            write_json_atomic(tmp / MANIFEST, rel.to_dict())
            os.replace(tmp, target)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return rel

    def set_withdrawn(self, code: int, withdrawn: bool, reason: str | None = None) -> Release:
        rel = self.get(code)
        if rel is None:
            raise ReleaseError(f"Release {code} nicht gefunden")
        rel.withdrawn = withdrawn
        rel.withdrawn_reason = (reason or "zurückgezogen")[:300] if withdrawn else None
        write_json_atomic(self._rdir(code) / MANIFEST, rel.to_dict())
        return rel

    def prune(self, keep: int, protect: set[int] | None = None) -> list[int]:
        """Removes releases beyond the newest ``keep`` (never ``protect``, e.g. what a phone still runs)."""
        protect = protect or set()
        removed = []
        for rel in self.all()[max(keep, 1) :]:
            if rel.version_code in protect:
                continue
            shutil.rmtree(self._rdir(rel.version_code), ignore_errors=True)
            removed.append(rel.version_code)
        for stale in self.dir.glob(".incoming-*") if self.dir.is_dir() else []:
            with contextlib.suppress(OSError):
                shutil.rmtree(stale)
        return removed


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
