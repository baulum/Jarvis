"""Credential store.

On macOS the system Keychain is used (via ``keyring``), service name ``jarvis``. On Windows keyring uses the
Credential Manager, on Linux the Secret Service; alternatively secrets can be injected through
``JARVIS_SECRET_<NAME>`` environment variables (e.g. from systemd credentials), which take
precedence. Secret values are never logged or returned by the API — only whether they exist.

The Windows Credential Manager limits one entry to 2560 bytes (1280 UTF-16 characters). Longer values (the FCM
service account JSON) are stored there as an index entry plus numbered parts; the Mac stores every value whole.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _secrets
import sys

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

SERVICE = "jarvis"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CHUNK_CHARS = 1200
_CHUNK_MARKER = "jarvis-chunked:"
logger = logging.getLogger(__name__)


class SecretStoreUnavailable(KeyringError):
    """No OS secret store on this host (e.g. a headless Linux server without Secret Service)."""


class SecretStore:
    def __init__(self, service: str = SERVICE, chunked: bool | None = None) -> None:
        self.service = service
        self.chunked = sys.platform == "win32" if chunked is None else chunked

    @staticmethod
    def _check(name: str) -> None:
        if not _NAME_RE.match(name):
            raise ValueError(f"Invalid secret name: {name!r}")

    @staticmethod
    def _env_key(name: str) -> str:
        return "JARVIS_SECRET_" + re.sub(r"[^A-Z0-9]", "_", name.upper())

    def get(self, name: str) -> str | None:
        self._check(name)
        env = os.environ.get(self._env_key(name))
        if env:
            return env
        try:
            value = keyring.get_password(self.service, name)
            if value is not None and self.chunked and value.startswith(_CHUNK_MARKER):
                return self._join(name, value)
            return value
        except KeyringError as e:
            logger.warning("Keychain read failed for %s: %s", name, type(e).__name__)
            return None

    def _join(self, name: str, index: str) -> str | None:
        try:
            count = int(index.removeprefix(_CHUNK_MARKER))
        except ValueError:
            return None
        parts = [keyring.get_password(self.service, f"{name}::{i}") for i in range(count)]
        if any(p is None for p in parts):
            logger.warning("Secret %s is incomplete (%d parts expected)", name, count)
            return None
        return "".join(p for p in parts if p is not None)

    def _drop_parts(self, name: str) -> None:
        try:
            index = keyring.get_password(self.service, name)
        except KeyringError:
            return
        if not index or not index.startswith(_CHUNK_MARKER):
            return
        try:
            count = int(index.removeprefix(_CHUNK_MARKER))
        except ValueError:
            return
        for i in range(count):
            try:
                keyring.delete_password(self.service, f"{name}::{i}")
            except PasswordDeleteError:
                continue

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise MissingSecretError(name)
        return value

    def set(self, name: str, value: str) -> None:
        self._check(name)
        if not value:
            raise ValueError("Secret value must not be empty")
        try:
            if not self.chunked:
                keyring.set_password(self.service, name, value)
                return
            self._drop_parts(name)
            if len(value) <= CHUNK_CHARS:
                keyring.set_password(self.service, name, value)
                return
            parts = [value[i : i + CHUNK_CHARS] for i in range(0, len(value), CHUNK_CHARS)]
            for i, part in enumerate(parts):
                keyring.set_password(self.service, f"{name}::{i}", part)
            keyring.set_password(self.service, name, f"{_CHUNK_MARKER}{len(parts)}")
        except NoKeyringError as e:
            raise SecretStoreUnavailable(
                f"Kein Schlüsselspeicher auf diesem System – {self._env_key(name)} als Umgebungsvariable setzen"
            ) from e

    def delete(self, name: str) -> bool:
        self._check(name)
        if self.chunked:
            self._drop_parts(name)
        try:
            keyring.delete_password(self.service, name)
            return True
        except PasswordDeleteError:
            return False

    def exists(self, name: str) -> bool:
        return bool(self.get(name))

    def ensure_random(self, name: str, nbytes: int = 32) -> str:
        """Return the secret, generating and storing a random one if missing."""
        value = self.get(name)
        if not value:
            value = _secrets.token_urlsafe(nbytes)
            self.set(name, value)
        return value


class MissingSecretError(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Secret '{name}' is not configured (jarvisctl secret set {name})")
        self.name = name


secret_store = SecretStore()
