import os
import sys

# Integration tests use a separate database; must be set before jarvis.config is imported.
os.environ.setdefault("JARVIS__DATABASE__NAME", "jarvis_test")

import pytest

# The coding runner pauses, resumes and stops agent runs through POSIX process groups (SIGSTOP/SIGCONT, killpg) and
# runs shell scripts; it stays on the Mac by design and WindowsAdapter reports ``coding.runner`` as unavailable.
CODING_RUNNER_ON_WINDOWS = "coding runner is macOS/Linux only (POSIX process groups) - coding.runner unavailable"


def pytest_collection_modifyitems(config, items):
    """e2e/integration tests need running services; run them with `pytest -m e2e` / `-m integration`."""
    selected = config.getoption("-m") or ""
    for item in items:
        for marker in ("e2e", "integration"):
            if marker in item.keywords and marker not in selected:
                item.add_marker(pytest.mark.skip(reason=f"{marker}: run with -m {marker}"))
        if sys.platform == "win32" and "coding_runner" in item.keywords:
            item.add_marker(pytest.mark.skip(reason=CODING_RUNNER_ON_WINDOWS))
