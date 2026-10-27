"""Which changed files reach the phone only through a new APK."""

from __future__ import annotations

from collections.abc import Iterable

APP_DIR = "apps/jarvis_mobile/"

# Below APP_DIR, but not part of the APK: tests, docs, lint settings, editor/IDE files.
IGNORED = (
    "apps/jarvis_mobile/test/",
    "apps/jarvis_mobile/integration_test/",
    "apps/jarvis_mobile/android/app/src/test/",
    "apps/jarvis_mobile/android/app/src/androidTest/",
    "apps/jarvis_mobile/README.md",
    "apps/jarvis_mobile/analysis_options.yaml",
    "apps/jarvis_mobile/.gitignore",
    "apps/jarvis_mobile/android/.gitignore",
    "apps/jarvis_mobile/.metadata",
)


def app_files(files: Iterable[str]) -> list[str]:
    """The files among ``files`` that end up in the APK (Dart code, Android project, pubspec, assets, shaders)."""
    out = []
    for f in files:
        f = f.strip()
        if f.startswith(APP_DIR) and not f.startswith(IGNORED) and not f.endswith(".md"):
            out.append(f)
    return out


def affects_app(files: Iterable[str] | None) -> bool:
    """Do the changes need a new APK? Unknown (None) counts as yes."""
    return True if files is None else bool(app_files(files))
