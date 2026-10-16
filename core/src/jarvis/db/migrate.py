"""Applies pending Alembic migrations at core start-up (so an app update never needs a manual step)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
CORE_DIR = Path(__file__).resolve().parents[3]


def _upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    ini = CORE_DIR / "alembic.ini"
    if not ini.exists():
        logger.warning("alembic.ini not found (%s); skipping migrations", ini)
        return
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(CORE_DIR / "migrations"))
    cfg.attributes["embedded"] = True
    command.upgrade(cfg, "head")


async def upgrade_to_head() -> None:
    await asyncio.to_thread(_upgrade)
