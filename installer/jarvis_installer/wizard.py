"""Guided first setup. Every step reports what it did and what is still open; nothing aborts the whole run.

Roles (more than one per computer possible):
- ``device``: install the Jarvis Device Agent (``uv tool install``), pair it with the Core, start it at log-on.
- ``client``: connection of the desktop app / browser to a Core (``client.json``: Core address + device token),
  install the built desktop app when it is there.
- ``core``: data folders, models (voice, speech recognition, wake word), Python environments, next steps
  (database, ``jarvisctl init``). On the Mac the core keeps using ``scripts/setup.sh``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_installer import deps, models
from jarvis_installer.host import Host, Runner


class UI:
    """Console interaction; tests replace it with scripted answers."""

    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{question}{suffix}: ").strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        hint = "J/n" if default else "j/N"
        answer = input(f"{question} ({hint}): ").strip().lower()
        if not answer:
            return default
        return answer in ("j", "ja", "y", "yes")

    def progress(self, label: str, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        print(f"\r  {label}: {pct:3d} % ({done / 1e6:.0f}/{total / 1e6:.0f} MB)", end="", flush=True)
        if done >= total:
            print()

    def progress_for(self, label: str) -> Callable[[int, int], None]:
        def report(done: int, total: int) -> None:
            self.progress(label, done, total)

        return report


@dataclass
class Options:
    repo: Path
    roles: set[str] | None = None
    core_url: str = ""
    pair_code: str = ""
    fingerprint: str = ""
    client_url: str = ""
    client_token: str = ""
    device_name: str = ""
    models: list[str] | None = None
    browser_automation: bool | None = None
    install_missing: bool | None = None  # winget (Windows); None = ask
    assume_yes: bool = False
    check_only: bool = False


@dataclass
class Summary:
    done: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    deps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"done": self.done, "open": self.open, "dependencies": self.deps}


Downloader = Callable[[models.ModelFile, Path, Callable[[int, int], None]], Path]


def _default_download(m: models.ModelFile, d: Path, p: Callable[[int, int], None]) -> Path:
    return models.download(m, d, progress=p)


class Wizard:
    def __init__(
        self,
        opts: Options,
        host: Host,
        ui: UI,
        run: Runner,
        download: Downloader = _default_download,
    ) -> None:
        self.opts = opts
        self.host = host
        self.ui = ui
        self.run = run
        self.download = download
        self.summary = Summary()

    # ------------------------------------------------------------------ helpers
    def confirm(self, question: str, default: bool = True) -> bool:
        return default if self.opts.assume_yes else self.ui.confirm(question, default)

    def ask(self, question: str, default: str = "") -> str:
        return default if self.opts.assume_yes and default else self.ui.ask(question, default)

    def exe(self, name: str) -> str:
        return name + ".exe" if self.host.os == "windows" else name

    # ------------------------------------------------------------------ flow
    def start(self) -> Summary:
        ui = self.ui
        ui.say("J.A.R.V.I.S. – Einrichtung")
        ui.say(f"System: {self.host.os} ({self.host.arch})")
        roles = self.choose_roles()
        self.check_dependencies(roles)
        if self.opts.check_only:
            return self.finish()
        if "core" in roles:
            self.setup_core()
        if "device" in roles:
            self.setup_device()
        if "client" in roles:
            self.setup_client()
        return self.finish()

    def choose_roles(self) -> set[str]:
        if self.opts.roles is not None:
            roles = set(self.opts.roles)
        else:
            self.ui.say(
                "\nRollen dieses Rechners:\n"
                "  client  – Desktop-App/Browser, verbunden mit dem Jarvis Core (z. B. auf dem Mac)\n"
                "  device  – Jarvis darf diesen Rechner steuern (Programme, Maus, Tastatur, Screenshots, Dateien)\n"
                "  core    – der Jarvis Core Server selbst läuft hier"
            )
            raw = self.ask("Rollen (durch Komma getrennt)", "client,device")
            roles = {r.strip() for r in raw.split(",") if r.strip()}
        unknown = roles - set(deps.ROLES)
        if unknown:
            self.summary.open.append(f"Unbekannte Rolle(n) ignoriert: {', '.join(sorted(unknown))}")
            roles -= unknown
        if "core" in roles and self.host.os == "mac":
            self.ui.say("Auf dem Mac richtet scripts/setup.sh den Core ein (unverändert) – Rolle „core“ übersprungen.")
            self.summary.open.append("Core auf dem Mac: scripts/setup.sh ausführen")
            roles.discard("core")
        self.roles = roles
        return roles

    def check_dependencies(self, roles: set[str]) -> None:
        self.ui.say("\nAbhängigkeiten:")
        statuses = deps.check_all(self.host, roles)
        for line in deps.report_lines(statuses, self.host):
            self.ui.say(line)
        missing = [s for s in statuses if not s.found]
        installable = [s for s in missing if s.dep.winget] if self.host.os == "windows" else []
        if installable and not self.opts.check_only:
            names = ", ".join(s.dep.label for s in installable)
            want = self.opts.install_missing
            if want is None:
                want = self.ui.confirm(f"Mit winget installieren: {names}?", True)
            if want:
                for s in installable:
                    ok, out = deps.winget_install(s.dep, self.run)
                    (self.summary.done if ok else self.summary.open).append(
                        f"{s.dep.label} {'installiert' if ok else 'nicht installiert: ' + out[-160:]}"
                    )
                statuses = deps.check_all(self.host, roles)
        for s in statuses:
            self.summary.deps.append(
                {
                    "id": s.dep.id,
                    "label": s.dep.label,
                    "found": s.found,
                    "required": s.required,
                    "where": s.where,
                    "hint": s.dep.hints.get(self.host.os, ""),
                }
            )
            if not s.found:
                prefix = "Fehlt (nötig)" if s.required else "Fehlt (empfohlen)"
                hint = s.dep.hints.get(self.host.os, "")
                self.summary.open.append(f"{prefix}: {s.dep.label}" + (f" → {hint}" if hint else ""))

    # ------------------------------------------------------------------ core
    def setup_core(self) -> None:
        h, ui = self.host, self.ui
        ui.say("\n▸ Jarvis Core Server")
        for d in (h.data_dir, h.data_dir / "bin", h.models_dir, h.vault_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.summary.done.append(f"Datenordner {h.data_dir}")
        groups = self.opts.models
        if groups is None:
            groups = ["tts", "wakeword"]
            choice = self.ask(
                "Spracherkennung: groß (574 MB, beste Qualität), klein (190 MB) oder keine (Whisper läuft auf dem Mac)",
                "groß",
            ).lower()
            if choice.startswith("g"):
                groups.append("stt")
            elif choice.startswith("k"):
                groups.append("stt-small")
        self.fetch_models(groups)
        core = self.opts.repo / "core"
        if self.confirm("Python-Umgebung des Core einrichten (uv sync, einige Minuten)?", True):
            code, out = self.run(["uv", "sync", "--directory", str(core), "--extra", "voice"])
            (self.summary.done if code == 0 else self.summary.open).append(
                "Core-Umgebung (uv sync)" if code == 0 else f"uv sync fehlgeschlagen: {out[-200:]}"
            )
        if "tts" in groups and self.confirm("Lokale Stimme (Piper-Server) einrichten?", True):
            venv = h.data_dir / "tts-local" / ".venv"
            req = self.opts.repo / "scripts" / "local-tts" / "requirements-piper.txt"
            python = venv / ("Scripts/python.exe" if h.os == "windows" else "bin/python")
            code, out = self.run(["uv", "venv", "--python", "3.12", str(venv)])
            if code == 0:
                code, out = self.run(["uv", "pip", "install", "--python", str(python), "-r", str(req)])
            (self.summary.done if code == 0 else self.summary.open).append(
                "Piper-Stimme eingerichtet" if code == 0 else f"Piper-Umgebung fehlgeschlagen: {out[-200:]}"
            )
        social = self.opts.repo / "social"
        if (social / "package.json").is_file() and self.confirm(
            "Social-Media-Videos: Remotion und Headless-Browser einrichten (npm ci, ~300 MB)?", True
        ):
            npm = "npm.cmd" if h.os == "windows" else "npm"
            code, out = self.run([npm, "ci", "--prefix", str(social), "--no-audit", "--no-fund"])
            if code == 0:
                code, out = self.run([npm, "run", "browser", "--prefix", str(social)])
            ok = "Remotion für Social-Videos eingerichtet"
            (self.summary.done if code == 0 else self.summary.open).append(
                ok if code == 0 else f"Remotion-Einrichtung fehlgeschlagen: {out[-200:]}"
            )
        start = "scripts\\windows\\start.ps1" if h.os == "windows" else "scripts/linux/start.sh"
        self.summary.open += [
            "Datenbank: PostgreSQL 17 + pgvector bereitstellen (oder JARVIS__DATABASE__HOST auf einen anderen Rechner)",
            "Claude Code anmelden: claude → /login",
            f"Geräte-Tokens anlegen: uv run --directory core jarvisctl init · Dienste starten: {start}",
        ]

    def fetch_models(self, groups: Sequence[str]) -> None:
        todo = models.missing(self.host.models_dir, list(groups))
        if not todo:
            self.summary.done.append(f"Modelle vorhanden ({', '.join(groups)})")
            return
        ok, needed, free = models.space_check(self.host.models_dir, todo)
        if not ok:
            self.summary.open.append(
                f"Zu wenig Speicherplatz für die Modelle: {needed / 1e9:.1f} GB nötig, {free / 1e9:.1f} GB frei"
            )
            return
        for m in todo:
            try:
                self.download(m, self.host.models_dir, self.ui.progress_for(m.label))
                self.summary.done.append(f"Modell geladen und geprüft: {m.label}")
            except (models.DownloadError, OSError) as e:
                self.summary.open.append(f"Modell {m.label}: {e}")

    # ------------------------------------------------------------------ device agent
    def setup_device(self) -> None:
        ui, h = self.ui, self.host
        ui.say("\n▸ Jarvis Device Agent")
        agent_src = self.opts.repo / "device_agent"
        browser = self.opts.browser_automation
        if browser is None:
            browser = self.confirm("Browser-Automatisierung (Playwright) mitinstallieren?", h.os == "windows")
        spec = f"{agent_src}[browser]" if browser else str(agent_src)
        code, out = self.run(["uv", "tool", "install", "--force", "--python", "3.12", spec])
        if code != 0:
            self.summary.open.append(f"Device Agent nicht installiert: {out[-200:]}")
            return
        code, bindir = self.run(["uv", "tool", "dir", "--bin"])
        agent = (
            str(Path(bindir.strip().splitlines()[-1]) / self.exe("jarvis-device"))
            if code == 0 and bindir.strip()
            else "jarvis-device"
        )
        self.summary.done.append("Device Agent installiert (jarvis-device)")
        if browser and h.os == "linux":
            code, tooldir = self.run(["uv", "tool", "dir"])
            if code == 0 and tooldir.strip():
                pw = Path(tooldir.strip().splitlines()[-1]) / "jarvis-device" / "bin" / "playwright"
                code, out = self.run([str(pw), "install", "chromium"])
                (self.summary.done if code == 0 else self.summary.open).append(
                    "Chromium für Playwright" if code == 0 else f"playwright install chromium: {out[-160:]}"
                )
        ui.say(
            "Kopplung: auf dem Rechner mit dem Core `jarvisctl devices pair` ausführen (zeigt Adresse, Code und "
            "Fingerabdruck)."
        )
        url = self.opts.core_url or self.ask("Core-Adresse (z. B. https://100.101.102.103:8767)", "")
        code_ = self.opts.pair_code or self.ask("Kopplungscode", "")
        fp = self.opts.fingerprint or self.ask("Fingerabdruck (leer bei Tailscale-Zertifikat)", "")
        if not url or not code_:
            self.summary.open.append(
                f"Device Agent koppeln: {agent} pair --core <Adresse> --code <Code> --fingerprint <SHA-256>"
            )
            return
        argv = [agent, "pair", "--core", url, "--code", code_]
        if fp:
            argv += ["--fingerprint", fp]
        if self.opts.device_name:
            argv += ["--name", self.opts.device_name]
        code, out = self.run(argv)
        if code != 0:
            self.summary.open.append(f"Kopplung fehlgeschlagen: {out[-200:]}")
            return
        self.summary.done.append("Device Agent gekoppelt")
        code, out = self.run([agent, "automations"])
        if code == 0 and out.strip():
            folder = out.strip().splitlines()[0].removeprefix("Automationen-Ordner: ")
            self.summary.done.append(f"Automationen-Ordner {folder} (Skripte dort startet Jarvis per Namen)")
        if self.confirm("Device Agent bei jeder Anmeldung automatisch starten?", True):
            code, out = self.run([agent, "autostart", "enable"])
            (self.summary.done if code == 0 else self.summary.open).append(
                "Autostart des Device Agents" if code == 0 else f"Autostart: {out[-160:]}"
            )

    # ------------------------------------------------------------------ client
    def setup_client(self) -> None:
        ui, h = self.ui, self.host
        ui.say("\n▸ Client (Desktop-App / Browser)")
        ui.say(
            "Empfohlen: auf dem Mac `tailscale serve --bg --https=443 https+insecure://localhost:8767` und in "
            "config/jarvis.yaml clients.remote_api: true – dann ist der Core unter "
            "https://<mac>.<tailnet>.ts.net mit gültigem Zertifikat erreichbar."
        )
        url = self.opts.client_url or self.ask("Core-Adresse für die App (https://<mac>.<tailnet>.ts.net)", "")
        token = self.opts.client_token or self.ask(
            'Geräte-Token (auf dem Mac: jarvisctl pair "<Name>" --role owner)', ""
        )
        if url and token:
            path = write_client_config(h.data_dir, url, token)
            self.summary.done.append(f"Verbindung der Desktop-App gespeichert ({path})")
        else:
            self.summary.open.append("Desktop-App verbinden: Core-Adresse und Token beim ersten Start eintragen")
        bundle = find_desktop_bundle(self.opts.repo, h.os)
        if bundle is None:
            self.summary.open.append(
                "Desktop-App bauen: cd desktop && pnpm install && pnpm tauri build "
                "(oder Browser-Client: <Core-Adresse>/app/ mit clients.web_client: true)"
            )
            return
        if h.os == "windows" and self.confirm(f"Desktop-App installieren ({bundle.name})?", True):
            argv = ["msiexec", "/i", str(bundle)] if bundle.suffix == ".msi" else [str(bundle)]
            code, out = self.run(argv)
            (self.summary.done if code == 0 else self.summary.open).append(
                "Desktop-App installiert" if code == 0 else f"Installation der App: {out[-160:]}"
            )
        else:
            self.summary.open.append(f"Desktop-App installieren: {bundle}")

    # ------------------------------------------------------------------ end
    def finish(self) -> Summary:
        ui = self.ui
        ui.say("\nErledigt:")
        for line in self.summary.done or ["–"]:
            ui.say(f"  ✓ {line}")
        ui.say("Offen:")
        for line in self.summary.open or ["–"]:
            ui.say(f"  • {line}")
        try:
            self.host.data_dir.mkdir(parents=True, exist_ok=True)
            (self.host.data_dir / "install-report.json").write_text(
                json.dumps(self.summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        return self.summary


def write_client_config(data_dir: Path, url: str, token: str) -> Path:
    """``client.json``: the desktop app connects to this Core instead of the local one (read by the Tauri shell)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "client.json"
    path.write_text(json.dumps({"core_url": url.rstrip("/"), "token": token}, indent=2), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def find_desktop_bundle(repo: Path, os_id: str) -> Path | None:
    bundle = repo / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    patterns = {"windows": ("nsis/*.exe", "msi/*.msi"), "linux": ("appimage/*.AppImage", "deb/*.deb")}.get(os_id, ())
    for pattern in patterns:
        hits = sorted(bundle.glob(pattern))
        if hits:
            return hits[-1]
    return None
