"""Local guards of the agent (defence in depth – the Core already applies risk levels and approvals).

- ``PathPolicy``: file actions, shell/Claude working directories only below the configured roots; never the
  agent's own config (token) and never key material such as ``~/.ssh``.
- ``KillSwitch``: engaged by the Core (``killswitch`` frame) *or* locally by creating the file ``KILL`` in the
  agent's config directory (``jarvis-device killswitch on``) – then only read-only actions run.
"""

from __future__ import annotations

from pathlib import Path

SENSITIVE_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store"}


class PolicyError(PermissionError):
    pass


class PathPolicy:
    def __init__(self, roots: list[Path], protected: list[Path] | None = None) -> None:
        self.roots = [r.resolve() for r in roots]
        self.protected = [p.resolve() for p in (protected or [])]

    def check(self, raw: str, *, must_exist: bool = False) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise PolicyError("Pfad fehlt")
        if "\x00" in raw:
            raise PolicyError("Ungültiger Pfad")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.home() / path
        resolved = path.resolve()  # symlinks are followed: a link out of the roots does not count as inside
        if not any(resolved == r or resolved.is_relative_to(r) for r in self.roots):
            raise PolicyError(f"{resolved} liegt außerhalb der freigegebenen Ordner")
        for p in self.protected:
            if resolved == p or resolved.is_relative_to(p):
                raise PolicyError("Die Konfiguration des Device Agents ist geschützt")
        if any(part.lower() in SENSITIVE_PARTS for part in resolved.parts):
            raise PolicyError("Schlüsselordner (.ssh, .gnupg, …) sind gesperrt")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(str(resolved))
        return resolved


class KillSwitch:
    def __init__(self, flag_file: Path) -> None:
        self.flag_file = flag_file
        self.remote = False  # mirrored from the Core

    @property
    def engaged(self) -> bool:
        return self.remote or self.flag_file.exists()

    def engage_local(self, reason: str = "lokal") -> None:
        self.flag_file.parent.mkdir(parents=True, exist_ok=True)
        self.flag_file.write_text(reason + "\n", encoding="utf-8")

    def release_local(self) -> None:
        self.flag_file.unlink(missing_ok=True)
