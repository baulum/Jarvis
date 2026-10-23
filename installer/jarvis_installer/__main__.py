"""``python -m jarvis_installer`` (started by scripts/windows/install.ps1 or scripts/linux/install.sh)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jarvis_installer import deps, models
from jarvis_installer.host import Host, run
from jarvis_installer.wizard import UI, Options, Wizard


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jarvis-install", description="J.A.R.V.I.S. einrichten (Windows, Linux)")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2], help="Jarvis-Projektordner")
    p.add_argument("--roles", help="client,device,core (Standard: fragen)")
    p.add_argument("--check", action="store_true", help="nur Abhängigkeiten prüfen und melden")
    p.add_argument("--json", action="store_true", help="Prüfergebnis als JSON ausgeben (mit --check)")
    p.add_argument("--models", help=f"Modellgruppen: {','.join(models.GROUPS)} (Standard: fragen)")
    p.add_argument("--download-models", action="store_true", help="nur Modelle laden (mit --models)")
    p.add_argument("--core-url", default="", help="Gateway des Core für den Device Agent (https://…:8767)")
    p.add_argument("--code", default="", help="Kopplungscode (jarvisctl devices pair)")
    p.add_argument("--fingerprint", default="", help="SHA-256 des Gateway-Zertifikats")
    p.add_argument("--device-name", default="")
    p.add_argument("--client-url", default="", help="Core-Adresse für Desktop-App/Browser")
    p.add_argument("--client-token", default="", help="Geräte-Token der Desktop-App")
    p.add_argument("--browser", choices=["yes", "no"], help="Playwright für den Device Agent")
    p.add_argument("--install-missing", choices=["yes", "no"], help="fehlende Programme per winget installieren")
    p.add_argument("--yes", action="store_true", help="Standardantworten verwenden")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    host = Host.current()
    roles = {r.strip() for r in a.roles.split(",")} if a.roles else None
    groups = [g.strip() for g in a.models.split(",") if g.strip()] if a.models else None
    if a.check and a.json:
        statuses = deps.check_all(host, roles or set(deps.ROLES))
        print(
            json.dumps(
                [
                    {
                        "id": s.dep.id,
                        "found": s.found,
                        "required": s.required,
                        "where": s.where,
                        "hint": s.dep.hints.get(host.os, ""),
                    }
                    for s in statuses
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if all(s.found for s in statuses if s.required) else 1
    if a.download_models:
        ui = UI()
        todo = models.missing(host.models_dir, groups or ["tts", "wakeword"])
        ok, needed, free = models.space_check(host.models_dir, todo)
        if not ok:
            print(f"Zu wenig Platz: {needed / 1e9:.1f} GB nötig, {free / 1e9:.1f} GB frei", file=sys.stderr)
            return 1
        for m in todo:
            models.download(m, host.models_dir, progress=ui.progress_for(m.label))
        print(f"Modelle bereit in {host.models_dir}")
        return 0
    opts = Options(
        repo=a.repo,
        roles=roles,
        core_url=a.core_url,
        pair_code=a.code,
        fingerprint=a.fingerprint,
        client_url=a.client_url,
        client_token=a.client_token,
        device_name=a.device_name,
        models=groups,
        browser_automation=None if a.browser is None else a.browser == "yes",
        install_missing=None if a.install_missing is None else a.install_missing == "yes",
        assume_yes=a.yes,
        check_only=a.check,
    )
    summary = Wizard(opts, host, UI(), run).start()
    required_open = [d for d in summary.deps if d["required"] and not d["found"]]
    return 1 if required_open else 0


if __name__ == "__main__":
    sys.exit(main())
