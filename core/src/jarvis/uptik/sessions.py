"""Encrypted browser sessions of the connected accounts.

A session is Playwright's storage state (cookies + local storage) after Sir logged in himself. Passwords are never
seen or stored: Sir types them into the platform's own page. The state is sealed with AES-256-GCM; the key is a
random secret in the OS secret store (macOS Keychain, Windows Credential Manager, Linux Secret Service or
``JARVIS_SECRET_UPTIK_SESSION_KEY``), derived per account with HKDF, the account id bound as associated data – a
file copied to another account does not open. On disk only ``<id>.session`` (0600) with nonce + ciphertext. Nothing
of it reaches logs, errors, the database, events or the memory.

Why not the Keychain itself: a session is often 20–200 KB; the Windows Credential Manager holds 2.5 KB per entry.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from jarvis.secrets import SecretStoreUnavailable, secret_store
from jarvis.uptik.model import PermanentError

KEY_SECRET = "uptik-session-key"  # noqa: S105 - name of the secret store entry, not a secret
_ID_RE = re.compile(r"^[a-z0-9-]{8,64}$")
_MAGIC = b"UPTIK1"


class SessionVault:
    def __init__(self, directory: Path, secret_name: str = KEY_SECRET) -> None:
        self.dir = directory
        self.secret_name = secret_name

    def _path(self, account_id: str) -> Path:
        if not _ID_RE.match(account_id):
            raise ValueError("invalid account id")
        return self.dir / f"{account_id}.session"

    def _key(self, account_id: str) -> bytes:
        try:
            master = secret_store.ensure_random(self.secret_name, 32)
        except SecretStoreUnavailable as e:
            raise PermanentError(str(e), code="secret_store") from e
        return HKDF(SHA256(), 32, b"jarvis-uptik-session", f"session v1|{account_id}".encode()).derive(master.encode())

    def save(self, account_id: str, state: dict[str, Any]) -> None:
        raw = json.dumps(state, separators=(",", ":")).encode()
        nonce = os.urandom(12)
        blob = _MAGIC + nonce + AESGCM(self._key(account_id)).encrypt(nonce, raw, account_id.encode())
        self.dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.dir.chmod(0o700)
        path = self._path(account_id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        tmp.replace(path)

    def load(self, account_id: str) -> dict[str, Any] | None:
        """The decrypted state, or None if there is none or it cannot be opened (key changed, file damaged)."""
        path = self._path(account_id)
        try:
            blob = path.read_bytes()
        except FileNotFoundError:
            return None
        if not blob.startswith(_MAGIC) or len(blob) < len(_MAGIC) + 28:
            return None
        nonce, ct = blob[len(_MAGIC) : len(_MAGIC) + 12], blob[len(_MAGIC) + 12 :]
        try:
            raw = AESGCM(self._key(account_id)).decrypt(nonce, ct, account_id.encode())
        except InvalidTag:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None

    def exists(self, account_id: str) -> bool:
        return self._path(account_id).is_file()

    def saved_at(self, account_id: str) -> str | None:
        try:
            return datetime.fromtimestamp(self._path(account_id).stat().st_mtime, UTC).isoformat(timespec="seconds")
        except FileNotFoundError:
            return None

    def delete(self, account_id: str) -> bool:
        path = self._path(account_id)
        if not path.exists():
            return False
        # overwrite before unlinking: the ciphertext is useless without the key, this is just tidy
        with contextlib.suppress(OSError), path.open("r+b") as f:
            f.write(b"\0" * path.stat().st_size)
        path.unlink(missing_ok=True)
        return True
