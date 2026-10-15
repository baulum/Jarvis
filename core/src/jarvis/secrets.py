"""Credential store.

On macOS the system Keychain is used (via ``keyring``), service name ``jarvis``. On a Linux
VPS keyring uses the Secret Service; alternatively secrets can be injected through
``JARVIS_SECRET_<NAME>`` environment variables (e.g. from systemd credentials), which take
precedence. Secret values are never logged or returned by the API — only whether they exist.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _secrets

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "jarvis"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
logger = logging.getLogger(__name__)


class SecretStore:
    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

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
            return keyring.get_password(self.service, name)
        except KeyringError as e:
            logger.warning("Keychain read failed for %s: %s", name, type(e).__name__)
            return None

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise MissingSecretError(name)
        return value

    def set(self, name: str, value: str) -> None:
        self._check(name)
        if not value:
            raise ValueError("Secret value must not be empty")
        keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> bool:
        self._check(name)
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
