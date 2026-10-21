from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

CORE = Path(__file__).resolve().parents[2]


def test_single_migration_head() -> None:
    # parallel coding jobs once both added revision f2a3b4c5d6e7; two heads break startup migration
    cfg = Config(str(CORE / "alembic.ini"))
    cfg.set_main_option("script_location", str(CORE / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
