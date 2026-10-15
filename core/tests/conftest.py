import os

# Integration tests use a separate database; must be set before jarvis.config is imported.
os.environ.setdefault("JARVIS__DATABASE__NAME", "jarvis_test")

import pytest  # noqa: E402


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    """e2e/integration tests need running services; run them with `pytest -m e2e` / `-m integration`."""
    selected = config.getoption("-m") or ""
    for item in items:
        for marker in ("e2e", "integration"):
            if marker in item.keywords and marker not in selected:
                item.add_marker(pytest.mark.skip(reason=f"{marker}: run with -m {marker}"))
