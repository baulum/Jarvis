"""Dependencies per role and OS: how to find them, why they are needed, how to install them.

``check_all`` never installs anything – it reports. ``winget_install`` (Windows) is only called by the wizard after
Sir agreed; on Linux/macOS the report shows the package-manager command.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis_installer.host import Host, Runner

ROLES = ("core", "client", "device")


@dataclass(frozen=True)
class Dependency:
    id: str
    label: str
    purpose: str
    roles: frozenset[str]  # roles that need it
    required: frozenset[str] = frozenset()  # roles for which it is mandatory (others: recommended)
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)  # os → programs in PATH
    paths: dict[str, tuple[str, ...]] = field(default_factory=dict)  # os → known install locations (globs allowed)
    winget: str | None = None
    hints: dict[str, str] = field(default_factory=dict)  # os → install command / link
    oses: frozenset[str] = frozenset({"windows", "mac", "linux"})


@dataclass(frozen=True)
class DepStatus:
    dep: Dependency
    found: bool
    where: str | None
    required: bool


def _all(*os_ids: str) -> frozenset[str]:
    return frozenset(os_ids)


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        "uv",
        "uv (Python-Umgebungen)",
        "installiert Core, Device Agent und Python",
        frozenset(ROLES),
        frozenset(ROLES),
        commands={"windows": ("uv",), "mac": ("uv",), "linux": ("uv",)},
        paths={
            "windows": ("~/.local/bin/uv.exe", "~/.cargo/bin/uv.exe"),
            "mac": ("~/.local/bin/uv",),
            "linux": ("~/.local/bin/uv", "~/.cargo/bin/uv"),
        },
        winget="astral-sh.uv",
        hints={
            "windows": 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
            "mac": "brew install uv",
            "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        },
    ),
    Dependency(
        "tailscale",
        "Tailscale",
        "verbindet Clients und Geräte sicher mit dem Core (auch unterwegs)",
        frozenset(ROLES),
        commands={"windows": ("tailscale",), "mac": ("tailscale",), "linux": ("tailscale",)},
        paths={"windows": ("%ProgramFiles%/Tailscale/tailscale.exe",), "mac": ("/Applications/Tailscale.app",)},
        winget="Tailscale.Tailscale",
        hints={
            "windows": "https://tailscale.com/download/windows",
            "mac": "https://tailscale.com/download/mac",
            "linux": "curl -fsSL https://tailscale.com/install.sh | sh",
        },
    ),
    Dependency(
        "obsidian",
        "Obsidian",
        "zeigt und bearbeitet Jarvis' Gedächtnis (Vault mit Markdown-Notizen)",
        frozenset({"core", "client"}),
        commands={"linux": ("obsidian",)},
        paths={
            "windows": (
                "%LOCALAPPDATA%/Programs/Obsidian/Obsidian.exe",
                "%LOCALAPPDATA%/Obsidian/Obsidian.exe",
                "%ProgramFiles%/Obsidian/Obsidian.exe",
            ),
            "mac": ("/Applications/Obsidian.app", "~/Applications/Obsidian.app"),
            "linux": (
                "/var/lib/flatpak/exports/bin/md.obsidian.Obsidian",
                "~/.local/share/flatpak/exports/bin/md.obsidian.Obsidian",
                "/snap/bin/obsidian",
                "~/Applications/Obsidian*.AppImage",
            ),
        },
        winget="Obsidian.Obsidian",
        hints={
            "windows": "https://obsidian.md/download",
            "mac": "brew install --cask obsidian",
            "linux": "flatpak install flathub md.obsidian.Obsidian",
        },
    ),
    Dependency(
        "claude",
        "Claude Code CLI",
        "Jarvis' Gehirn (Core) bzw. Claude-Code-Aufträge auf diesem Gerät",
        frozenset({"core", "device"}),
        frozenset({"core"}),
        commands={"windows": ("claude",), "mac": ("claude",), "linux": ("claude",)},
        paths={
            "windows": ("~/.local/bin/claude.exe", "%APPDATA%/npm/claude.cmd"),
            "mac": ("~/.local/bin/claude", "~/.claude/local/claude"),
            "linux": ("~/.local/bin/claude", "~/.claude/local/claude"),
        },
        hints={
            "windows": "irm https://claude.ai/install.ps1 | iex   (danach: claude → /login)",
            "mac": "curl -fsSL https://claude.ai/install.sh | bash   (danach: claude → /login)",
            "linux": "curl -fsSL https://claude.ai/install.sh | bash   (danach: claude → /login)",
        },
    ),
    Dependency(
        "git",
        "Git",
        "Projekte, Coding-Jobs und Updates von Jarvis",
        frozenset({"core", "device"}),
        frozenset({"core"}),
        commands={"windows": ("git",), "mac": ("git",), "linux": ("git",)},
        paths={"windows": ("%ProgramFiles%/Git/cmd/git.exe",)},
        winget="Git.Git",
        hints={
            "windows": "https://git-scm.com/download/win",
            "mac": "xcode-select --install",
            "linux": "sudo apt install git",
        },
    ),
    Dependency(
        "postgresql",
        "PostgreSQL 17 + pgvector",
        "Datenbank des Core (Gespräche, Aufgaben, Gedächtnis-Index)",
        frozenset({"core"}),
        frozenset({"core"}),
        commands={"windows": ("psql",), "mac": ("psql",), "linux": ("psql",)},
        paths={
            "windows": ("%ProgramFiles%/PostgreSQL/*/bin/psql.exe",),
            "mac": ("/opt/homebrew/opt/postgresql@17/bin/psql",),
        },
        winget="PostgreSQL.PostgreSQL.17",
        hints={
            "windows": "winget install PostgreSQL.PostgreSQL.17 – pgvector einfacher per Docker: "
            "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=… pgvector/pgvector:pg17 "
            "(oder database.host auf den Mac zeigen lassen)",
            "mac": "brew install postgresql@17 pgvector",
            "linux": "sudo apt install postgresql postgresql-17-pgvector",
        },
    ),
    Dependency(
        "ffmpeg",
        "FFmpeg",
        "wandelt Sprachnachrichten und Audio für die Spracherkennung um",
        frozenset({"core"}),
        commands={"windows": ("ffmpeg",), "mac": ("ffmpeg",), "linux": ("ffmpeg",)},
        winget="Gyan.FFmpeg",
        hints={
            "windows": "winget install Gyan.FFmpeg",
            "mac": "brew install ffmpeg",
            "linux": "sudo apt install ffmpeg",
        },
    ),
    Dependency(
        "whisper",
        "whisper.cpp (whisper-server)",
        "lokale Spracherkennung (Modell lädt der Installer)",
        frozenset({"core"}),
        commands={"windows": ("whisper-server",), "mac": ("whisper-server",), "linux": ("whisper-server",)},
        paths={
            "windows": ("~/AppData/Roaming/Jarvis/bin/whisper-server.exe",),
            "linux": ("~/.local/share/jarvis/bin/whisper-server",),
        },
        hints={
            "windows": "https://github.com/ggml-org/whisper.cpp/releases (whisper-bin-x64.zip → "
            "%APPDATA%\\Jarvis\\bin) – oder voice.stt.server_url auf den Mac zeigen lassen",
            "mac": "brew install whisper-cpp",
            "linux": "whisper.cpp bauen (cmake) – oder voice.stt.server_url auf den Mac zeigen lassen",
        },
    ),
    Dependency(
        "node",
        "Node.js + pnpm",
        "nur zum Bauen der Desktop-App aus dem Quellcode",
        frozenset({"client"}),
        commands={"windows": ("pnpm",), "mac": ("pnpm",), "linux": ("pnpm",)},
        winget="OpenJS.NodeJS.LTS",
        hints={
            "windows": "winget install OpenJS.NodeJS.LTS; npm install -g pnpm",
            "mac": "brew install node pnpm",
            "linux": "sudo apt install nodejs npm && sudo npm install -g pnpm",
        },
    ),
    Dependency(
        "powershell",
        "Windows PowerShell",
        "Startmenü-Programme, Benachrichtigungen, Lautstärke und Sprachausgabe des Device Agents",
        frozenset({"device"}),
        frozenset({"device"}),
        commands={"windows": ("powershell",)},
        hints={"windows": "Teil von Windows – Windows PowerShell 5.1 aktivieren"},
        oses=_all("windows"),
    ),
    Dependency(
        "xdotool",
        "xdotool + wmctrl",
        "Maus, Tastatur und Fenster unter X11",
        frozenset({"device"}),
        commands={"linux": ("xdotool",)},
        hints={"linux": "sudo apt install xdotool wmctrl"},
        oses=_all("linux"),
    ),
    Dependency(
        "xclip",
        "xclip / wl-clipboard",
        "Zwischenablage",
        frozenset({"device"}),
        commands={"linux": ("xclip", "xsel", "wl-copy")},
        hints={"linux": "sudo apt install xclip wl-clipboard"},
        oses=_all("linux"),
    ),
    Dependency(
        "notify-send",
        "libnotify (notify-send)",
        "Benachrichtigungen",
        frozenset({"device"}),
        commands={"linux": ("notify-send",)},
        hints={"linux": "sudo apt install libnotify-bin"},
        oses=_all("linux"),
    ),
    Dependency(
        "atspi",
        "AT-SPI (python3-gi)",
        "Bedienelemente lesen/drücken und unter Wayland das aktive Fenster erkennen",
        frozenset({"device"}),
        paths={
            "linux": (
                "/usr/lib/*/girepository-1.0/Atspi-2.0.typelib",
                "/usr/lib/girepository-1.0/Atspi-2.0.typelib",
                "/usr/lib64/girepository-1.0/Atspi-2.0.typelib",
            )
        },
        hints={"linux": "sudo apt install python3-gi gir1.2-atspi-2.0"},
        oses=_all("linux"),
    ),
    Dependency(
        "espeak",
        "espeak-ng",
        "Systemstimme als letzte Rückfallstufe (Core) bzw. Vorlesen auf dem Gerät",
        frozenset({"core", "device"}),
        commands={"linux": ("espeak-ng", "espeak")},
        hints={"linux": "sudo apt install espeak-ng"},
        oses=_all("linux"),
    ),
    Dependency(
        "portaudio",
        "PortAudio",
        "Mikrofon und Lautsprecher für den Sprachdienst",
        frozenset({"core"}),
        paths={
            "linux": (
                "/usr/lib/x86_64-linux-gnu/libportaudio.so.2",
                "/usr/lib/aarch64-linux-gnu/libportaudio.so.2",
                "/usr/lib/libportaudio.so.2",
                "/usr/lib64/libportaudio.so.2",
            )
        },
        hints={"linux": "sudo apt install libportaudio2"},
        oses=_all("linux"),
    ),
)


def check(dep: Dependency, host: Host, roles: set[str]) -> DepStatus:
    required = bool(dep.required & roles)
    for cmd in dep.commands.get(host.os, ()):
        if found := host.which(cmd):
            return DepStatus(dep, True, found, required)
    for raw in dep.paths.get(host.os, ()):
        path = host.expand(raw)
        if any(ch in raw for ch in "*?["):
            hits = host.globber(str(path))
            if hits:
                return DepStatus(dep, True, hits[0], required)
        elif host.exists(path):
            return DepStatus(dep, True, str(path), required)
    return DepStatus(dep, False, None, required)


def check_all(host: Host, roles: set[str]) -> list[DepStatus]:
    return [check(d, host, roles) for d in DEPENDENCIES if d.roles & roles and host.os in d.oses]


def report_lines(statuses: list[DepStatus], host: Host) -> list[str]:
    lines = []
    for st in statuses:
        if st.found:
            lines.append(f"  ✓ {st.dep.label:<30} {st.where}")
            continue
        mark = "✗" if st.required else "!"
        kind = "fehlt (nötig)" if st.required else "fehlt (empfohlen)"
        lines.append(f"  {mark} {st.dep.label:<30} {kind} – {st.dep.purpose}")
        if hint := st.dep.hints.get(host.os):
            lines.append(f"      → {hint}")
    return lines


def winget_install(dep: Dependency, run: Runner) -> tuple[bool, str]:
    if not dep.winget:
        return False, "kein winget-Paket"
    code, out = run(
        [
            "winget",
            "install",
            "--id",
            dep.winget,
            "--exact",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
    )
    return code == 0, out[-400:]
