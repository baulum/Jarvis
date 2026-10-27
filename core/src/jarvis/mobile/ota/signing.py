"""The one release signing key of the Android app.

Android installs an update over an existing app only if both carry the same signature – a new key means
uninstalling (losing the pairing). So the key is created exactly once and never replaced automatically:

- keystore (PKCS12, RSA 4096) in ``<data_dir>/android-signing/`` (folder 0700, file 0600) – outside the repository
- its password in the Keychain (``mobile.app_update.keystore_password_secret``), never in a file, argv or log:
  keytool reads it with ``-storepass:env``, Gradle from the build process' environment
- ``signing.json`` next to it records the certificate's SHA-256 fingerprint (not secret) for status and checks

If the keystore disappears after releases were published with it, nothing is generated: the status names the
problem (restore from a backup) instead of silently switching to a key the phones would reject.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.mobile.ota.releases import now_iso, write_json_atomic
from jarvis.mobile.ota.toolchain import Runner
from jarvis.secrets import SecretStore, secret_store

KEYSTORE = "release.p12"
INFO = "signing.json"
KEY_ALIAS = "jarvis-release"
PASS_ENV = "JARVIS_KEYSTORE_PASS"  # noqa: S105 - variable name; only in the environment of keytool / the Gradle build
DNAME = "CN=J.A.R.V.I.S. Mobile, OU=Jarvis, O=Jarvis"


class SigningError(RuntimeError):
    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(slots=True)
class SigningKey:
    store_file: Path
    alias: str
    fingerprint: str
    password: str = field(repr=False)

    def gradle_env(self) -> dict[str, str]:
        """Read by apps/jarvis_mobile/android/app/build.gradle.kts (release signingConfig)."""
        return {
            "JARVIS_SIGNING_STORE_FILE": str(self.store_file),
            "JARVIS_SIGNING_STORE_PASSWORD": self.password,
            "JARVIS_SIGNING_KEY_ALIAS": self.alias,
            "JARVIS_SIGNING_KEY_PASSWORD": self.password,
        }


def normalize_fingerprint(fp: str) -> str:
    """``AB:CD:…`` / ``abcd…`` → lowercase hex without separators."""
    return fp.replace(":", "").replace(" ", "").strip().lower()


class SigningManager:
    def __init__(
        self, directory: Path, secret_name: str, *, runner: Runner | None = None, store: SecretStore | None = None
    ) -> None:
        self.dir = directory
        self.secret_name = secret_name
        self.runner = runner or Runner()
        self.secrets = store or secret_store

    @property
    def store_file(self) -> Path:
        return self.dir / KEYSTORE

    def info(self) -> dict[str, Any]:
        """Status without touching the Keychain or running keytool."""
        try:
            data = json.loads((self.dir / INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {
            "exists": self.store_file.is_file(),
            "fingerprint": data.get("fingerprint"),
            "created_at": data.get("created_at"),
            "location": str(self.dir),
        }

    async def ensure(self, keytool: str, expected: str | None) -> SigningKey:
        """The signing key; created on the very first build. ``expected``: fingerprint of the releases already
        published – the key must match it, and is never generated anew once releases exist."""
        expected = normalize_fingerprint(expected) if expected else None
        recorded = self.info().get("fingerprint")
        if not self.store_file.is_file():
            if expected or recorded:
                raise SigningError(
                    "keystore_missing",
                    f"Der Signaturschlüssel fehlt ({self.store_file}), es gibt aber schon Releases damit. Ein neuer "
                    "Schlüssel würde Updates auf dem Handy unmöglich machen – bitte aus der Sicherung "
                    "wiederherstellen (Datei + Schlüsselbund-Eintrag).",
                )
            return await self._generate(keytool)
        password = self.secrets.get(self.secret_name)
        if not password:
            raise SigningError(
                "keystore_password_missing",
                f"Das Passwort des Signaturschlüssels fehlt im Schlüsselbund ({self.secret_name}).",
            )
        fp = await self.fingerprint(keytool, password)
        for want in (expected, recorded):
            if want and fp != normalize_fingerprint(want):
                raise SigningError(
                    "keystore_mismatch",
                    "Der Signaturschlüssel passt nicht zu den bisherigen Releases (anderer Fingerabdruck) – "
                    "nicht gebaut, damit das Handy keine fremd signierte App bekommt.",
                )
        if not recorded:
            self._write_info(fp)
        return SigningKey(self.store_file, KEY_ALIAS, fp, password)

    async def fingerprint(self, keytool: str, password: str) -> str:
        """SHA-256 over the DER certificate – the same value Android/apksigner report for the APK signer."""
        code, der = await self.runner.output(
            [keytool, "-exportcert", "-alias", KEY_ALIAS, "-keystore", str(self.store_file),
             "-storetype", "PKCS12", "-storepass:env", PASS_ENV],
            env={**os.environ, PASS_ENV: password},
            timeout_s=60,
        )  # fmt: skip
        if code != 0 or not der:
            raise SigningError("keystore_unreadable", "Signaturschlüssel nicht lesbar (falsches Passwort oder defekt).")
        return hashlib.sha256(der).hexdigest()

    async def _generate(self, keytool: str) -> SigningKey:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir.chmod(0o700)
        password = secrets.token_urlsafe(32)
        self.secrets.set(self.secret_name, password)  # first: a key without its password would be useless
        tmp = self.dir / f".{KEYSTORE}.tmp"
        with contextlib.suppress(OSError):
            tmp.unlink()
        code, out = await self.runner.run(
            [keytool, "-genkeypair", "-keystore", str(tmp), "-storetype", "PKCS12", "-alias", KEY_ALIAS,
             "-keyalg", "RSA", "-keysize", "4096", "-validity", "36500", "-dname", DNAME,
             "-storepass:env", PASS_ENV, "-keypass:env", PASS_ENV],
            env={**os.environ, PASS_ENV: password},
            timeout_s=180,
            redact=[password],
        )  # fmt: skip
        if code != 0 or not tmp.is_file():
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise SigningError("keystore_create_failed", f"Signaturschlüssel konnte nicht erzeugt werden: {out[-300:]}")
        tmp.chmod(0o600)
        os.replace(tmp, self.store_file)
        fp = await self.fingerprint(keytool, password)
        self._write_info(fp)
        return SigningKey(self.store_file, KEY_ALIAS, fp, password)

    def _write_info(self, fp: str) -> None:
        write_json_atomic(self.dir / INFO, {"fingerprint": fp, "alias": KEY_ALIAS, "created_at": now_iso()})
