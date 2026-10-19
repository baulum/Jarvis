"""Backups: PostgreSQL dump + Obsidian vault + configuration, encrypted, with retention.

One archive per run (``jarvis-backup-YYYYmmdd-HHMMSS.tar.gz[.enc]``):

* ``database.dump``  – ``pg_dump -Fc`` (restore with ``pg_restore``)
* ``vault/``         – the long-term memory as Markdown
* ``config/``        – jarvis.yaml, permissions.yaml, personality.yaml, mcp_servers.yaml
* ``state/``         – small runtime settings (approval mode, model choice, alert policy, usage)

Secrets (Keychain, token files) are never part of a backup. The archive is encrypted with AES-256-GCM
(chunked, so truncation is detected); the key lives in the Keychain (``backup-key``) – export it once with
``jarvisctl backup key`` and keep it in a password manager, otherwise the backups die with the Mac.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import struct
import tarfile
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jarvis.agent.persona import TZ
from jarvis.config import CONFIG_DIR
from jarvis.events import bus
from jarvis.secrets import secret_store

if TYPE_CHECKING:
    from jarvis.config import Settings
    from jarvis.jobs.notify import Notifier

logger = logging.getLogger(__name__)
MAGIC = b"JBK1"
CHUNK = 4 * 1024 * 1024
STATE_FILES = ("approval-mode.json", "agent-model.json", "alert-policy.json", "agent-usage.json")
CONFIG_FILES = ("jarvis.yaml", "permissions.yaml", "personality.yaml", "mcp_servers.yaml")
NAME_PREFIX = "jarvis-backup-"


# --------------------------------------------------------------------------- encryption
def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    """``MAGIC | base nonce(8)`` then per chunk ``len(4) | AES-GCM(chunk)``; AAD carries index and last-flag."""
    aes = AESGCM(key)
    base = os.urandom(8)
    with src.open("rb") as fin, dst.open("wb") as fout:
        fout.write(MAGIC + base)
        index = 0
        chunk = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            last = not nxt
            ct = aes.encrypt(base + struct.pack(">I", index), chunk, struct.pack(">IB", index, last))
            fout.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            chunk, index = nxt, index + 1


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    aes = AESGCM(key)
    with src.open("rb") as fin, dst.open("wb") as fout:
        head = fin.read(12)
        if head[:4] != MAGIC:
            raise ValueError("Keine verschlüsselte Jarvis-Sicherung")
        base, index = head[4:], 0
        while True:
            raw = fin.read(4)
            if len(raw) < 4:
                raise ValueError("Sicherung abgeschnitten (letzter Block fehlt)")
            ct = fin.read(struct.unpack(">I", raw)[0])
            last = fin.read(1) == b""
            if not last:
                fin.seek(-1, 1)
            try:
                pt = aes.decrypt(base + struct.pack(">I", index), ct, struct.pack(">IB", index, last))
            except Exception as e:
                raise ValueError(
                    "Entschlüsselung fehlgeschlagen (falscher Schlüssel, beschädigt oder abgeschnitten)"
                ) from e
            fout.write(pt)
            if last:
                return
            index += 1


# --------------------------------------------------------------------------- retention
def select_keep(names: list[str], keep_recent: int, keep_weekly: int) -> set[str]:
    """Newest ``keep_recent`` backups plus the newest one of each of the last ``keep_weekly`` ISO weeks."""
    parsed: list[tuple[datetime, str]] = []
    for n in names:
        try:
            parsed.append((datetime.strptime(n.removeprefix(NAME_PREFIX)[:15], "%Y%m%d-%H%M%S"), n))
        except ValueError:
            continue  # not ours – never touched
    parsed.sort(reverse=True)
    keep = {n for _, n in parsed[:keep_recent]}
    seen_weeks: list[tuple[int, int]] = []
    for ts, n in parsed:
        wk = ts.isocalendar()[:2]
        if wk in seen_weeks:
            continue
        seen_weeks.append(wk)
        if len(seen_weeks) <= keep_weekly:
            keep.add(n)
    return keep


class BackupService:
    def __init__(self, settings: Settings, notifier: Notifier | None = None) -> None:
        self.settings = settings
        self.notifier = notifier
        self.cfg = settings.backup
        self.dir = Path(self.cfg.directory).expanduser()
        self.status_file = settings.paths.data_dir / "backup-status.json"
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.last: dict[str, Any] = {}
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError):
            self.last = json.loads(self.status_file.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.cfg.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def key(self) -> bytes:
        return hashlib.sha256(secret_store.ensure_random(self.cfg.key_secret, 32).encode()).digest()

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(TZ)
        hh, mm = (int(x) for x in self.cfg.at.split(":"))
        scheduled = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < scheduled:
            return False
        ok = self.last.get("ok_at")
        return not ok or datetime.fromisoformat(ok) < scheduled

    async def _loop(self) -> None:
        await asyncio.sleep(90)  # let the core settle after start
        while True:
            try:
                if self.due():
                    await self.run("scheduled")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Backup loop failed")
            await asyncio.sleep(600)

    # ------------------------------------------------------------------ run
    async def run(self, reason: str = "manual") -> dict[str, Any]:
        if self._lock.locked():
            raise RuntimeError("Eine Sicherung läuft bereits")
        async with self._lock:
            t0 = time.monotonic()
            try:
                path = await asyncio.to_thread(self._build_sync)
                size = path.stat().st_size
                kept = await asyncio.to_thread(self._prune)
                self.last = {
                    "ok": True,
                    "ok_at": datetime.now(TZ).isoformat(),
                    "file": path.name,
                    "size": size,
                    "duration_s": round(time.monotonic() - t0, 1),
                    "encrypted": self.cfg.encrypt,
                    "reason": reason,
                    "kept": kept,
                }
            except Exception as e:
                logger.exception("Backup failed")
                self.last = {**self.last, "ok": False, "error": str(e)[:300], "failed_at": datetime.now(TZ).isoformat()}
                if self.notifier is not None:
                    await self.notifier.notify(
                        "backup.failed", "Sicherung fehlgeschlagen", str(e)[:300], level="warning"
                    )
            self._save()
            await bus.publish("backup.finished", ok=bool(self.last.get("ok")), file=self.last.get("file"))
            return self.last

    def _save(self) -> None:
        self.status_file.write_text(json.dumps(self.last), encoding="utf-8")

    def _dump_database(self, target: Path) -> None:
        import subprocess

        db = self.settings.database
        env = {**os.environ, "PGPASSWORD": secret_store.get(db.password_secret) or ""}
        exe = shutil.which(self.cfg.pg_dump) or self.cfg.pg_dump
        proc = subprocess.run(
            [exe, "-Fc", "-h", db.host, "-p", str(db.port), "-U", db.user, "-f", str(target), db.name],
            env=env, capture_output=True, text=True, timeout=1800, check=False,
        )  # fmt: skip
        if proc.returncode != 0:
            raise RuntimeError(f"pg_dump: {proc.stderr.strip()[:240] or proc.returncode}")

    def _build_sync(self) -> Path:
        s = self.settings
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir.chmod(0o700)
        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        with tempfile.TemporaryDirectory(dir=self.dir) as td:
            tmp = Path(td)
            self._dump_database(tmp / "database.dump")
            archive = tmp / f"{NAME_PREFIX}{stamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(tmp / "database.dump", arcname="database.dump")
                if s.paths.vault_dir.exists():
                    tar.add(s.paths.vault_dir, arcname="vault", filter=_skip_hidden_state)
                for name in CONFIG_FILES:
                    f = CONFIG_DIR / name
                    if f.exists():
                        tar.add(f, arcname=f"config/{name}")
                for name in STATE_FILES:
                    f = s.paths.data_dir / name
                    if f.exists():
                        tar.add(f, arcname=f"state/{name}")
                meta = tmp / "BACKUP.json"
                meta.write_text(json.dumps({"created": datetime.now(TZ).isoformat(), "version": 1}))
                tar.add(meta, arcname="BACKUP.json")
            final = self.dir / (archive.name + (".enc" if self.cfg.encrypt else ""))
            if self.cfg.encrypt:
                encrypt_file(archive, final, self.key())
            else:
                shutil.move(archive, final)
            final.chmod(0o600)
        return final

    def _prune(self) -> int:
        files = {f.name: f for f in self.dir.glob(f"{NAME_PREFIX}*") if f.is_file()}
        keep = select_keep(list(files), self.cfg.keep_recent, self.cfg.keep_weekly)
        for name, f in files.items():
            if name not in keep and name.endswith((".tar.gz", ".tar.gz.enc")):
                f.unlink(missing_ok=True)
        return len(keep)

    # ------------------------------------------------------------------ info
    def list(self) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.dir.glob(f"{NAME_PREFIX}*"), reverse=True):
            if f.is_file():
                st = f.stat()
                out.append(
                    {
                        "file": f.name,
                        "size": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime, TZ).isoformat(),
                    }
                )
        return out

    def status(self) -> dict[str, Any]:
        files = self.list()
        newest = files[0]["modified"] if files else None
        age_ok = bool(newest and datetime.now(TZ) - datetime.fromisoformat(newest) < timedelta(hours=36))
        return {
            "enabled": self.cfg.enabled,
            "directory": str(self.dir),
            "at": self.cfg.at,
            "encrypted": self.cfg.encrypt,
            "last": self.last,
            "count": len(files),
            "recent_enough": age_ok,
            "files": files[:20],
        }


def _skip_hidden_state(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if "/.obsidian/workspace" in info.name or info.name.endswith(".DS_Store") else info


def restore_archive(archive: Path, target: Path, key: bytes | None) -> Path:
    """Decrypt (if needed) and unpack into ``target`` – never touches the live database or vault."""
    target.mkdir(parents=True, exist_ok=True)
    src = archive
    with tempfile.TemporaryDirectory() as td:
        if archive.name.endswith(".enc"):
            if key is None:
                raise ValueError("Schlüssel fehlt")
            src = Path(td) / archive.name.removesuffix(".enc")
            decrypt_file(archive, src, key)
        with tarfile.open(src, "r:gz") as tar:
            tar.extractall(target, filter="data")
    return target
