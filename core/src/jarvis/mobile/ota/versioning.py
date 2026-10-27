"""versionCode and versionName of OTA releases.

Android installs an update only if its versionCode is higher than the installed one, so the code is strictly
increasing: every successful build takes ``max(last issued, existing releases, pubspec build) + 1`` and the
highest code ever issued is persisted – a deleted or withdrawn release never frees its number.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Manual builds with `--split-per-abi` got 1000·ABI + build number (arm64: 2001). OTA releases start above every
# such build, so the first OTA release is always newer than whatever was installed by hand.
FIRST_VERSION_CODE = 3000
MAX_VERSION_CODE = 2_100_000_000  # Google Play's limit; Android itself allows up to 2^31-1

_PUBSPEC = re.compile(r"^version:\s*['\"]?(\d+\.\d+\.\d+)(?:\+(\d+))?['\"]?\s*$", re.MULTILINE)
_NAME = re.compile(r"^(\d+\.\d+\.\d+)\+(\d+)\.([0-9a-f]{7,40})$")


class VersionError(ValueError):
    pass


def pubspec_version(text: str) -> tuple[str, int]:
    """(semver, build number) from pubspec.yaml; ("1.0.0", 0) if missing."""
    m = _PUBSPEC.search(text)
    if not m:
        return "1.0.0", 0
    return m.group(1), int(m.group(2) or 0)


def next_version_code(last_issued: int, existing: Iterable[int] = (), pubspec_build: int = 0) -> int:
    code = max(FIRST_VERSION_CODE, last_issued + 1, max(existing, default=0) + 1, pubspec_build + 1)
    if code > MAX_VERSION_CODE:
        raise VersionError(f"versionCode {code} über dem Höchstwert {MAX_VERSION_CODE}")
    return code


def version_name(semver: str, code: int, commit: str) -> str:
    """``1.0.0+3001.ab12cd3`` – semver from pubspec, build metadata: versionCode and commit."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", semver):
        raise VersionError(f"ungültige Version {semver!r} in pubspec.yaml")
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise VersionError(f"ungültiger Commit {commit!r}")
    return f"{semver}+{code}.{commit[:7]}"


def parse_version_name(name: str) -> tuple[str, int, str] | None:
    """(semver, code, short commit) of an OTA version name; None for other names (e.g. manual builds)."""
    m = _NAME.match(name or "")
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


def is_upgrade(candidate: int, installed: int | None) -> bool:
    """Android refuses downgrades: only a strictly higher versionCode is an update."""
    return installed is None or candidate > installed
