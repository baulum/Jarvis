"""``jarvisctl`` — administration CLI for the Jarvis core."""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import platform
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import httpx

from jarvis.config import REPO_ROOT, get_settings
from jarvis.secrets import secret_store

CORE_DIR = REPO_ROOT / "core"


def _token_file(name: str) -> Path:
    return get_settings().paths.data_dir / f"{name}.token"


def _write_token(name: str, token: str) -> Path:
    path = _token_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _api(method: str, path: str, **kw: Any) -> Any:
    s = get_settings()
    token = _token_file("desktop").read_text().strip()
    r = httpx.request(
        method,
        f"http://127.0.0.1:{s.server.port}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
        **kw,
    )
    r.raise_for_status()
    return r.json()


async def _with_db(coro_fn: Any) -> Any:
    from jarvis.db import dispose_engine, init_engine

    init_engine(get_settings())
    try:
        return await coro_fn()
    finally:
        await dispose_engine()


def cmd_mobile_pair(args: argparse.Namespace) -> None:
    """One-time pairing QR for the companion app (valid 10 minutes, owner-only API)."""
    import segno

    res = _api("POST", "/api/mobile/pairings", json={})
    print("\nJ.A.R.V.I.S. – Smartphone koppeln")
    print("In der App „QR-Code scannen“ wählen (gültig bis " + res["expires_at"][11:16] + " UTC).\n")
    segno.make(res["payload"], error="m").terminal(compact=True, border=2)
    print("\nAdressen:     " + ", ".join(res["urls"]))
    print("Fingerabdruck: " + str(res["fingerprint"]))
    print("\nOhne Kamera: diesen Text in der App unter „Kopplungstext einfügen“ eingeben:\n" + res["payload"] + "\n")
    if args.svg:
        Path(args.svg).write_text(res["svg"], encoding="utf-8")
        print(f"SVG gespeichert: {args.svg}")


def cmd_mobile_status(_: argparse.Namespace) -> None:
    print(json.dumps(_api("GET", "/api/mobile/status"), indent=2, ensure_ascii=False))
    for d in _api("GET", "/api/mobile/devices"):
        state = "widerrufen" if d["revoked"] else ("Push ✓" if d["push"] else "kein Push")
        print(f"  • {d['name']} ({d['model']}) – {state}, zuletzt {d['last_seen_at'] or '–'}")


def cmd_migrate(_: argparse.Namespace) -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=CORE_DIR, check=True)


def cmd_init(args: argparse.Namespace) -> None:
    """Create the owner desktop device and the voice daemon device (tokens in 0600 files)."""
    from jarvis.security.auth import create_device

    async def run() -> None:
        for name, role in (("desktop", "owner"), ("voice", "voice_owner")):
            path = _token_file(name)
            if path.exists() and not args.force:
                print(f"✓ {name}-Token existiert bereits ({path})")
                continue
            _, token = await create_device(f"{name}@{platform.node()}", role)
            print(f"✓ Gerät '{name}' ({role}) angelegt → {_write_token(name, token)}")

    asyncio.run(_with_db(run))


def cmd_setup_hermes(args: argparse.Namespace) -> None:
    from jarvis.agent.hermes_setup import MCP_TOKEN_SECRET, provision_profile
    from jarvis.app_state import AppState
    from jarvis.security.auth import create_device

    async def run() -> None:
        state = AppState(get_settings())
        token = secret_store.get(MCP_TOKEN_SECRET)
        if not token or args.rotate:
            _, token = await create_device("hermes-agent", "agent")
            secret_store.set(MCP_TOKEN_SECRET, token)
        info = provision_profile(state, token)
        print(f"✓ Hermes-Profil konfiguriert: {info['profile_dir']} (API-Port {info['api_port']})")
        print(f"  MCP-Server 'jarvis' → http://127.0.0.1:{state.settings.server.port}/mcp")

    asyncio.run(_with_db(run))


def cmd_secret(args: argparse.Namespace) -> None:
    if args.action == "set":
        value = args.value if args.value is not None else getpass.getpass(f"Wert für '{args.name}': ")
        if args.from_file:
            value = Path(args.from_file).read_text(encoding="utf-8").strip()
        secret_store.set(args.name, value)
        print(f"✓ '{args.name}' im Schlüsselbund gespeichert")
    elif args.action == "status":
        print(f"{args.name}: {'gesetzt' if secret_store.exists(args.name) else 'fehlt'}")
    elif args.action == "delete":
        print("✓ gelöscht" if secret_store.delete(args.name) else "nicht vorhanden")


def cmd_pair(args: argparse.Namespace) -> None:
    from jarvis.security.auth import create_device

    async def run() -> None:
        _, token = await create_device(args.name, args.role)
        print(f"Gerät '{args.name}' ({args.role}) angelegt. Token (nur jetzt sichtbar):\n{token}")
        print(
            "\nFür die Desktop-App/den Browser auf einem anderen Rechner: Core-Adresse (Tailscale) und dieses Token "
            "im Installer bzw. unter „Verbindung“ eintragen (siehe docs/WINDOWS.md)."
        )

    asyncio.run(_with_db(run))


def cmd_status(_: argparse.Namespace) -> None:
    print(json.dumps(_api("GET", "/api/system/status"), indent=2, ensure_ascii=False))


def cmd_killswitch(args: argparse.Namespace) -> None:
    flag = get_settings().paths.data_dir / "KILL"
    if args.state == "on":
        flag.write_text("cli: manuell\n", encoding="utf-8")  # works even if the core is down
        try:
            _api("POST", "/api/system/killswitch", json={"engaged": True, "reason": "CLI"})
        except Exception:  # noqa: S110 - file flag is authoritative
            pass
        print("⛔ Not-Aus aktiv")
    else:
        _api("POST", "/api/system/killswitch", json={"engaged": False})
        print("✓ Not-Aus aufgehoben")


def cmd_chat(args: argparse.Namespace) -> None:
    res = _api("POST", "/api/chat", json={"message": " ".join(args.message), "wait": True, "channel": "api"})
    print(res.get("text"))
    if res.get("tools"):
        print(
            f"\n[Werkzeuge: {', '.join(str(t.get('name')) for t in res['tools'] if t.get('event') == 'tool_started')}]"
        )


def cmd_phone_test(args: argparse.Namespace) -> None:
    """LOCAL TEST MODE: plays a WAV (or speaks text via `say`) into a simulated call."""
    import websockets

    from jarvis.voice.audio import resample, ulaw_encode

    info = _api("POST", "/api/telephony/test-call")
    print(f"⚠ {info['notice']} (call {info['call_id']})")
    utterances: list[bytes] = []
    for item in args.say:
        out = Path(f"/tmp/jarvis-phone-{len(utterances)}.aiff")  # noqa: S108
        subprocess.run(["say", "-v", "Anna", "-o", str(out), item], check=True)
        pcm = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(out), "-ar", "8000", "-ac", "1", "-f", "s16le", "-"],
            check=True,
            capture_output=True,
        ).stdout
        utterances.append(pcm)
    if args.wav:
        with wave.open(args.wav) as w:
            import numpy as np

            wav_pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            utterances.append(resample(wav_pcm, w.getframerate(), 8000).tobytes())

    async def run() -> None:
        import numpy as np

        received = bytearray()
        async with websockets.connect(info["ws_url"]) as ws:
            await ws.send(
                json.dumps(
                    {
                        "event": "start",
                        "streamSid": "local",
                        "start": {
                            "streamSid": "local",
                            "customParameters": {"call_id": info["call_id"], "token": info["stream_token"]},
                        },
                    }
                )
            )

            async def reader() -> None:
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("event") == "media":
                        received.extend(base64.b64decode(data["media"]["payload"]))

            rtask = asyncio.create_task(reader())
            silence = ulaw_encode(np.zeros(160, dtype=np.int16))

            async def send_silence(seconds: float) -> None:
                for _ in range(int(seconds * 50)):
                    await ws.send(
                        json.dumps({"event": "media", "media": {"payload": base64.b64encode(silence).decode()}})
                    )
                    await asyncio.sleep(0.02)

            await send_silence(args.wait)
            for pcm in utterances:
                arr = np.frombuffer(pcm, dtype=np.int16)
                for i in range(0, len(arr), 160):
                    frame = ulaw_encode(arr[i : i + 160])
                    await ws.send(
                        json.dumps({"event": "media", "media": {"payload": base64.b64encode(frame).decode()}})
                    )
                    await asyncio.sleep(0.02)
                await send_silence(args.wait)
            await ws.send(json.dumps({"event": "stop"}))
            rtask.cancel()
        if args.out:
            from jarvis.voice.audio import pcm_to_wav, ulaw_decode

            Path(args.out).write_bytes(pcm_to_wav(ulaw_decode(bytes(received)), 8000))
            print(f"Antwort-Audio gespeichert: {args.out} ({len(received) / 8000:.1f}s)")

    asyncio.run(run())
    calls = _api("GET", "/api/telephony/calls")
    for turn in calls[0].get("transcript") or []:
        print(f"{turn['role']:>7}: {turn['text']}")


def cmd_doctor(_: argparse.Namespace) -> None:
    import shutil

    s = get_settings()
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    for b in ("claude", "git", "ffmpeg", "whisper-server", "psql", "cloudflared"):
        check(
            f"binary {b}",
            shutil.which(b) is not None or (b == "psql" and Path("/opt/homebrew/opt/postgresql@17/bin/psql").exists()),
        )
    from jarvis.coding.runner import resolve_command

    coding_cmd = resolve_command(s.coding.claude_command, s.coding.claude_aliases)
    check(f"coding command {s.coding.claude_command}", coding_cmd is not None, " ".join(coding_cmd or []))
    check("secret postgres", secret_store.exists(s.database.password_secret))
    check("desktop token", _token_file("desktop").exists(), "jarvisctl init")
    check("bridge token", _token_file("bridge").exists(), "JarvisBridge einmal starten")
    model = s.paths.data_dir / "models" / s.voice.stt.model
    check("whisper model", model.exists(), str(model))
    for f in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"):
        check(f"wake model {f}", (s.paths.data_dir / "models" / f).exists())
    emb_cache = s.paths.data_dir / "models" / "fastembed"
    check("embedding model (memory)", any(emb_cache.glob("**/*.onnx")), "lädt beim ersten Start des Core (~1,1 GB)")
    # --- security hygiene
    if sys.platform != "win32":  # Windows protects the profile with ACLs; Unix modes mean nothing there
        for name in ("desktop.token", "voice.token", "bridge.token", "agent-mcp.json"):
            tf = s.paths.data_dir / name
            if tf.exists():
                mode = tf.stat().st_mode & 0o777
                check(f"permissions {name}", mode & 0o077 == 0, f"chmod 600 '{tf}' (aktuell {mode:o})")
        check("data dir private", s.paths.data_dir.stat().st_mode & 0o077 == 0, f"chmod 700 '{s.paths.data_dir}'")
    root = Path(__file__).resolve().parents[4]
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", ".env", ".env.local", "*.pem", "*.key", "*.token"],
        capture_output=True, text=True, check=False,
    ).stdout.split()  # fmt: skip
    check("keine Secrets im Git", not tracked, f"getrackt: {', '.join(tracked)} → git rm --cached")
    # --- backups & quota
    from jarvis.backup.service import BackupService

    bk = BackupService(s)
    if s.backup.enabled:
        st = bk.status()
        check(
            "Sicherung vorhanden und aktuell (<36 h)", st["recent_enough"], f"jarvisctl backup run  ({st['directory']})"
        )
        if s.backup.encrypt:
            check(
                "Backup-Schlüssel im Schlüsselbund",
                secret_store.exists(s.backup.key_secret),
                "wird beim 1. Backup angelegt",
            )
    from jarvis.agent.usage import UsageTracker

    pressure = UsageTracker(s.paths.data_dir / "agent-usage.json").pressure()
    check("Claude-Kontingent unter 90 %", pressure < 0.9, f"aktuell {round(pressure * 100)} %")
    for name, ok, detail in checks:
        print(f"{'✓' if ok else '✗'} {name}{'' if ok else f'  → {detail}'}")
    sys.exit(0 if all(ok for _, ok, _ in checks) else 1)


def cmd_voice(_: argparse.Namespace) -> None:
    from jarvis.voice.daemon import main as voice_main

    voice_main()


def cmd_backup(a: argparse.Namespace) -> None:
    import asyncio
    from pathlib import Path

    from jarvis.backup.service import BackupService, restore_archive
    from jarvis.config import get_settings
    from jarvis.secrets import secret_store

    settings = get_settings()
    if a.action == "key":
        print(secret_store.ensure_random(settings.backup.key_secret, 32))
        print("\nDiesen Schlüssel in einem Passwortmanager ablegen – ohne ihn sind verschlüsselte Sicherungen wertlos.")
        return
    if a.action == "restore":
        if not a.file:
            sys.exit("Sicherungsdatei angeben: jarvisctl backup restore <Datei> [--to Ordner]")
        import hashlib

        key = hashlib.sha256((a.key or secret_store.require(settings.backup.key_secret)).encode()).digest()
        target = restore_archive(Path(a.file).expanduser(), Path(a.to).expanduser(), key)
        print(f"Entpackt nach {target}")
        print(f"Datenbank zurückspielen:\n  pg_restore --clean --if-exists -d jarvis {target}/database.dump")
        return
    svc = BackupService(settings)
    if a.action == "list":
        for f in svc.list():
            print(f"{f['modified']}  {f['size'] / 1e6:8.1f} MB  {f['file']}")
        return
    print(asyncio.run(svc.run("cli")))


def cmd_devices(a: argparse.Namespace) -> None:
    """Jarvis Device Agents: pairing code, list, capabilities of this Core host, revoke."""
    if a.action == "pair":
        info = _api("POST", "/api/devices/agents/pairing", json={"name": a.name or ""})
        print("Kopplungscode für den Jarvis Device Agent (gültig 10 Minuten):\n")
        print(f"  {info['code']}\n")
        print("Auf dem zu steuernden Rechner ausführen:\n")
        print(f"  {info['command']}\n")
        if info.get("urls"):
            print("Erreichbare Adressen des Core (Tailscale zuerst): " + ", ".join(info["urls"]))
        return
    if a.action == "list":
        for d in _api("GET", "/api/devices/agents"):
            missing = [c for c, st in (d.get("capabilities") or {}).items() if st.get("status") == "unavailable"]
            state = "online " if d.get("online") else "offline"
            print(f"{state}  {d['name']:<24} {d.get('platform') or '-':<8} {d['id']}"
                  + (f"  (nicht verfügbar: {', '.join(missing)})" if missing else ""))  # fmt: skip
        return
    if a.action == "capabilities":
        from jarvis.platform import create_adapter

        adapter = create_adapter(data_dir=get_settings().paths.data_dir)
        info = adapter.info()
        print(f"Plattform: {info['label']} ({info['session']}, {info['machine']})")
        for cap, st in adapter.capabilities().to_dict().items():
            mark = {"available": "✓", "degraded": "~", "unavailable": "✗"}[st["status"]]
            print(f"{mark} {cap:<22} {st['label']}" + (f"  – {st['reason']}" if st["reason"] else ""))
        return
    if a.action == "revoke":
        if not a.name:
            sys.exit("ID des Geräts angeben: jarvisctl devices revoke <id>")
        print(_api("DELETE", f"/api/devices/agents/{a.name}"))


def cmd_serve(_: argparse.Namespace) -> None:
    from jarvis.main import run

    run()


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvisctl", description="J.A.R.V.I.S. Verwaltung")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Core-Server starten").set_defaults(fn=cmd_serve)
    sub.add_parser("migrate", help="Datenbank-Migrationen ausführen").set_defaults(fn=cmd_migrate)
    sub.add_parser("voice", help="Voice-Daemon starten (Wake Word, Mikrofon, Sprachausgabe)").set_defaults(fn=cmd_voice)
    i = sub.add_parser("init", help="Desktop- und Voice-Gerät anlegen")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)
    h = sub.add_parser("setup-hermes", help="Hermes-Profil + MCP-Anbindung konfigurieren")
    h.add_argument("--rotate", action="store_true", help="MCP-Token neu erzeugen")
    h.set_defaults(fn=cmd_setup_hermes)
    sc = sub.add_parser("secret", help="Zugangsdaten im Schlüsselbund verwalten")
    sc.add_argument("action", choices=["set", "status", "delete"])
    sc.add_argument("name")
    sc.add_argument("--value")
    sc.add_argument("--from-file")
    sc.set_defaults(fn=cmd_secret)
    pr = sub.add_parser("pair", help="Weiteres Gerät koppeln")
    pr.add_argument("name")
    pr.add_argument("--role", default="operator", choices=["operator", "viewer", "owner"],
                    help="owner nur für Sirs eigene Rechner (Desktop-App auf dem Windows-PC)")
    pr.set_defaults(fn=cmd_pair)
    mp = sub.add_parser("mobile-pair", help="Smartphone (J.A.R.V.I.S.-App) koppeln: QR-Code anzeigen")
    mp.add_argument("--svg", help="QR-Code zusätzlich als SVG-Datei speichern")
    mp.set_defaults(fn=cmd_mobile_pair)
    sub.add_parser("mobile-status", help="Mobile-Status").set_defaults(fn=cmd_mobile_status)
    dv = sub.add_parser("devices", help="Jarvis Device Agents: pair | list | capabilities | revoke")
    dv.add_argument("action", choices=["pair", "list", "capabilities", "revoke"])
    dv.add_argument("name", nargs="?", help="pair: Gerätename (Vorschlag); revoke: Geräte-ID")
    dv.set_defaults(fn=cmd_devices)
    bk = sub.add_parser("backup", help="Sicherungen: run | list | key | restore")
    bk.add_argument("action", choices=["run", "list", "key", "restore"])
    bk.add_argument("file", nargs="?")
    bk.add_argument("--to", default="~/jarvis-restore")
    bk.add_argument("--key", help="Schlüssel (sonst aus dem Schlüsselbund)")
    bk.set_defaults(fn=cmd_backup)
    sub.add_parser("status", help="Systemstatus").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="Installation prüfen").set_defaults(fn=cmd_doctor)
    k = sub.add_parser("killswitch", help="Not-Aus")
    k.add_argument("state", choices=["on", "off"])
    k.set_defaults(fn=cmd_killswitch)
    c = sub.add_parser("chat", help="Nachricht an Jarvis senden")
    c.add_argument("message", nargs="+")
    c.set_defaults(fn=cmd_chat)
    pt = sub.add_parser("phone-test", help="LOKALER TESTMODUS für die Telefonie")
    pt.add_argument("--say", action="append", default=[], help="Text, der als Anrufer gesprochen wird")
    pt.add_argument("--wav")
    pt.add_argument("--wait", type=float, default=6.0, help="Sekunden Stille zwischen Äußerungen")
    pt.add_argument("--out", help="Antwort-Audio als WAV speichern")
    pt.set_defaults(fn=cmd_phone_test)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
