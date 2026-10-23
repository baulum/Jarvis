"""``jarvis-device`` – command line of the Jarvis Device Agent.

jarvis-device pair --core https://mac.tailnet.ts.net:8767 --code <Code> --fingerprint <SHA-256>
jarvis-device run                      # connect and execute the Core's commands (foreground)
jarvis-device status | capabilities
jarvis-device roots list|add|remove <Ordner>
jarvis-device config terminal on|off | browser on|off | claude <Programm>
jarvis-device killswitch on|off        # local emergency stop: only read-only actions
jarvis-device autostart enable|disable
jarvis-device unpair
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_device import __version__
from jarvis_device.config import AgentConfig, config_dir, config_path

if TYPE_CHECKING:
    from jarvis_device.dispatcher import Dispatcher


def _load() -> tuple[AgentConfig, Path]:
    path = config_path()
    return AgentConfig.load(path), path


def build_runtime(cfg: AgentConfig) -> Dispatcher:
    from jarvis_device.backends import create_backend
    from jarvis_device.dispatcher import Dispatcher
    from jarvis_device.security import KillSwitch, PathPolicy

    home = config_dir()
    policy = PathPolicy(cfg.roots(), protected=[home])
    return Dispatcher(create_backend(), cfg, policy, KillSwitch(home / "KILL"))


def _setup_logging(verbose: bool) -> None:
    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(home / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)


def cmd_pair(a: argparse.Namespace) -> int:
    from jarvis_device.backends import create_backend
    from jarvis_device.connection import PairingFailed, normalize_fingerprint, register

    cfg, path = _load()
    name = a.name or cfg.name
    fp = normalize_fingerprint(a.fingerprint or "")
    try:
        res = register(a.core.rstrip("/"), a.code.strip(), name, fp, create_backend().platform)
    except PairingFailed as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    cfg.core_url, cfg.fingerprint, cfg.name = a.core.rstrip("/"), fp, name
    cfg.device_id, cfg.token = str(res["device_id"]), str(res["token"])
    cfg.save(path)
    print(f"✓ Gekoppelt als „{name}“ ({cfg.device_id}). Konfiguration: {path}")
    print("  Starten: jarvis-device run   ·   Beim Anmelden starten: jarvis-device autostart enable")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from jarvis_device.connection import AgentConnection, Unauthorized

    _setup_logging(a.verbose)
    cfg, _ = _load()
    if not cfg.paired:
        print(
            "Noch nicht gekoppelt: jarvis-device pair --core <URL> --code <Code> [--fingerprint <SHA-256>]",
            file=sys.stderr,
        )
        return 2
    dispatcher = build_runtime(cfg)
    log = logging.getLogger("jarvis_device")
    conn = AgentConnection(cfg, dispatcher, on_state=lambda s, d: log.info("Status: %s %s", s, d))
    try:
        asyncio.run(conn.run_forever())
    except Unauthorized as e:
        log.error("%s", e)
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    cfg, path = _load()
    print(f"Jarvis Device Agent {__version__}")
    print(f"Konfiguration: {path}")
    print(json.dumps(cfg.public(), indent=2, ensure_ascii=False))
    kill = config_dir() / "KILL"
    print(f"Lokaler Not-Aus: {'AKTIV' if kill.exists() else 'aus'}")
    return 0


def cmd_capabilities(_: argparse.Namespace) -> int:
    from jarvis_device.protocol import CAPABILITY_LABELS

    cfg, _path = _load()
    dispatcher = build_runtime(cfg)
    print(f"Plattform: {dispatcher.backend.platform} ({dispatcher.backend.session()})")
    for cap, st in dispatcher.capabilities().items():
        mark = {"available": "✓", "degraded": "~", "unavailable": "✗"}[st["status"]]
        reason = f"  – {st['reason']}" if st["reason"] else ""
        print(f"{mark} {cap:<20} {CAPABILITY_LABELS.get(cap, cap)}{reason}")
    return 0


def cmd_roots(a: argparse.Namespace) -> int:
    cfg, path = _load()
    if a.action == "list":
        for r in cfg.allowed_roots:
            print(r)
        return 0
    if not a.path:
        print("Ordner angeben", file=sys.stderr)
        return 1
    target = str(Path(a.path).expanduser().resolve())
    if a.action == "add":
        if not Path(target).is_dir():
            print(f"{target} ist kein Ordner", file=sys.stderr)
            return 1
        if target not in cfg.allowed_roots:
            cfg.allowed_roots.append(target)
    else:
        cfg.allowed_roots = [r for r in cfg.allowed_roots if str(Path(r).expanduser().resolve()) != target]
    cfg.save(path)
    print("Freigegebene Ordner: " + (", ".join(cfg.allowed_roots) or "keine"))
    return 0


def cmd_config(a: argparse.Namespace) -> int:
    cfg, path = _load()
    if a.key == "terminal":
        if a.value not in ("on", "off"):
            print("terminal on|off", file=sys.stderr)
            return 1
        cfg.terminal = a.value
    elif a.key == "browser":
        if a.value not in ("on", "off"):
            print("browser on|off", file=sys.stderr)
            return 1
        cfg.browser_automation = a.value == "on"
    elif a.key == "claude":
        cfg.claude_binary = a.value
    cfg.save(path)
    print("Gespeichert. Der laufende Agent meldet die Änderung spätestens nach 5 Minuten (oder nach Neustart).")
    return 0


def cmd_killswitch(a: argparse.Namespace) -> int:
    from jarvis_device.security import KillSwitch

    ks = KillSwitch(config_dir() / "KILL")
    if a.state == "on":
        ks.engage_local("jarvis-device killswitch on")
        print("Not-Aus aktiv: nur noch lesende Aktionen.")
    else:
        ks.release_local()
        print("Lokaler Not-Aus aufgehoben.")
    return 0


def cmd_autostart(a: argparse.Namespace) -> int:
    from jarvis_device import autostart

    try:
        print(autostart.enable() if a.action == "enable" else autostart.disable())
    except (RuntimeError, OSError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    return 0


def cmd_unpair(_: argparse.Namespace) -> int:
    cfg, path = _load()
    cfg.token, cfg.device_id = "", ""
    cfg.save(path)
    print(
        "Token gelöscht. Im Core das Gerät zusätzlich widerrufen (Desktop-App › Geräte oder jarvisctl devices revoke)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis-device", description="Jarvis Device Agent")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("pair", help="Mit dem Jarvis Core koppeln")
    pr.add_argument("--core", required=True, help="Adresse des Core-Gateways, z. B. https://100.101.102.103:8767")
    pr.add_argument("--code", required=True, help="Kopplungscode (jarvisctl devices pair)")
    pr.add_argument("--fingerprint", default="", help="SHA-256 des Gateway-Zertifikats (aus dem Kopplungsbefehl)")
    pr.add_argument("--name", default="", help="Gerätename (Standard: Rechnername)")
    pr.set_defaults(fn=cmd_pair)
    r = sub.add_parser("run", help="Verbinden und Befehle ausführen")
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status", help="Konfiguration anzeigen").set_defaults(fn=cmd_status)
    sub.add_parser("capabilities", help="Fähigkeiten dieses Rechners prüfen").set_defaults(fn=cmd_capabilities)
    ro = sub.add_parser("roots", help="Freigegebene Ordner")
    ro.add_argument("action", choices=["list", "add", "remove"])
    ro.add_argument("path", nargs="?")
    ro.set_defaults(fn=cmd_roots)
    co = sub.add_parser("config", help="Einstellungen: terminal on|off, browser on|off, claude <Programm>")
    co.add_argument("key", choices=["terminal", "browser", "claude"])
    co.add_argument("value")
    co.set_defaults(fn=cmd_config)
    ks = sub.add_parser("killswitch", help="Lokaler Not-Aus")
    ks.add_argument("state", choices=["on", "off"])
    ks.set_defaults(fn=cmd_killswitch)
    au = sub.add_parser("autostart", help="Beim Anmelden starten")
    au.add_argument("action", choices=["enable", "disable"])
    au.set_defaults(fn=cmd_autostart)
    sub.add_parser("unpair", help="Token löschen").set_defaults(fn=cmd_unpair)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
