"""Sir's global Claude Code skills, made available to the agent and the coding runner.

Sources: user skills (``~/.claude/skills/<name>/SKILL.md``) and the skills of Claude Code
plugins that are enabled in ``~/.claude/settings.json``. Sir enables/disables each skill in
the desktop app; the selection lives in ``claude-skills.json``. Enabled skills are exposed to
Claude Code as ONE Jarvis-managed plugin directory of symlinks (``--plugin-dir``) — so the
agents keep running ``--restricted`` (no foreign settings, hooks or MCP servers) and still see
exactly the skills Sir switched on. Defaults: user skills on, plugin skills off.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
_SAFE = re.compile(r"[^a-z0-9-]+")


@dataclass(slots=True)
class ClaudeSkill:
    id: str  # "user/<dir>" or "plugin:<plugin>/<dir>"
    name: str
    description: str
    source: str  # "user" | "plugin:<name>"
    path: Path

    def to_dict(self, enabled: bool) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "enabled": enabled,
        }


def _frontmatter(skill_md: Path) -> dict[str, Any]:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        data = yaml.safe_load(text.split("---", 2)[1]) or {}
    except (yaml.YAMLError, IndexError):
        return {}
    return data if isinstance(data, dict) else {}


class SkillCatalog:
    def __init__(self, data_dir: Path, claude_home: Path | None = None) -> None:
        self.claude_home = claude_home or Path.home() / ".claude"
        self.state_file = data_dir / "claude-skills.json"
        self.plugin_dir = data_dir / "skills-plugin"
        self._overrides: dict[str, bool] = self._load()

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, bool]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {str(k): bool(v) for k, v in (data.get("overrides") or {}).items()}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.state_file.write_text(json.dumps({"overrides": self._overrides}, indent=1), encoding="utf-8")

    def is_enabled(self, skill: ClaudeSkill) -> bool:
        if skill.id in self._overrides:
            return self._overrides[skill.id]
        return skill.source == "user"

    # ------------------------------------------------------------------ discovery
    def discover(self) -> list[ClaudeSkill]:
        skills: list[ClaudeSkill] = []
        user_dir = self.claude_home / "skills"
        for md in sorted(user_dir.glob("*/SKILL.md")):
            skills.append(self._skill(md, "user", f"user/{md.parent.name}"))
        for plugin, install in self._enabled_plugins():
            for md in sorted(install.glob("skills/*/SKILL.md")):
                skills.append(self._skill(md, f"plugin:{plugin}", f"plugin:{plugin}/{md.parent.name}"))
        return skills

    def _skill(self, md: Path, source: str, sid: str) -> ClaudeSkill:
        fm = _frontmatter(md)
        return ClaudeSkill(
            id=sid,
            name=str(fm.get("name") or md.parent.name),
            description=str(fm.get("description") or "").strip()[:400],
            source=source,
            path=md.parent,
        )

    def _enabled_plugins(self) -> list[tuple[str, Path]]:
        try:
            settings = json.loads((self.claude_home / "settings.json").read_text(encoding="utf-8"))
            installed = json.loads(
                (self.claude_home / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        enabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v}
        out: list[tuple[str, Path]] = []
        for key, entries in (installed.get("plugins") or {}).items():
            if key not in enabled or not entries:
                continue
            path = Path(entries[-1].get("installPath", ""))
            if path.is_dir():
                out.append((key.split("@", 1)[0], path))
        return out

    # ------------------------------------------------------------------ API
    def items(self) -> list[dict[str, Any]]:
        return [s.to_dict(self.is_enabled(s)) for s in self.discover()]

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        if not any(s.id == skill_id for s in self.discover()):
            raise LookupError(f"Unbekannter Skill: {skill_id}")
        self._overrides[skill_id] = enabled
        self._save()
        self.build_plugin_dir()

    def enabled_names(self) -> list[str]:
        return [s.name for s in self.discover() if self.is_enabled(s)]

    def build_plugin_dir(self) -> Path:
        """(Re)create the Jarvis plugin dir with symlinks to every enabled skill."""
        skills_dir = self.plugin_dir / "skills"
        if self.plugin_dir.exists():
            shutil.rmtree(self.plugin_dir)
        (self.plugin_dir / ".claude-plugin").mkdir(parents=True)
        skills_dir.mkdir()
        (self.plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "jarvis-skills",
                    "version": "1.0.0",
                    "description": "Von Sir in Jarvis aktivierte globale Claude-Skills",
                }
            ),
            encoding="utf-8",
        )
        used: set[str] = set()
        for skill in self.discover():
            if not self.is_enabled(skill):
                continue
            base = _SAFE.sub("-", skill.path.name.lower()).strip("-") or "skill"
            name, n = base, 2
            while name in used:
                name, n = f"{base}-{n}", n + 1
            used.add(name)
            (skills_dir / name).symlink_to(skill.path, target_is_directory=True)
        logger.info("Claude skills plugin rebuilt: %d skills", len(used))
        return self.plugin_dir
