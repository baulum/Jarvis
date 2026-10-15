"""``jarvisctl`` — administration CLI for the Jarvis core."""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import os
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
            _, token = await create_device(f"{name}@{os.uname().nodename}", role)
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

            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            utterances.append(resample(pcm, w.getframerate(), 8000).tobytes())

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

    for b in ("ollama", "claude", "git", "ffmpeg", "whisper-server", "psql"):
        check(
            f"binary {b}",
            shutil.which(b) is not None or (b == "psql" and Path("/opt/homebrew/opt/postgresql@17/bin/psql").exists()),
        )
    check("hermes", bool(shutil.which("hermes") or (Path.home() / ".local/bin/hermes").exists()))
    check("secret postgres", secret_store.exists(s.database.password_secret))
    check("secret hermes-api-key", secret_store.exists(s.hermes.api_key_secret), "jarvisctl setup-hermes")
    check("desktop token", _token_file("desktop").exists(), "jarvisctl init")
    check("bridge token", _token_file("bridge").exists(), "JarvisBridge einmal starten")
    model = s.paths.data_dir / "models" / s.voice.stt.model
    check("whisper model", model.exists(), str(model))
    for f in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"):
        check(f"wake model {f}", (s.paths.data_dir / "models" / f).exists())
    try:
        r = httpx.get(s.llm.ollama_url + "/api/tags", timeout=3).json()
        names = {m["name"].split(":latest")[0] for m in r.get("models", [])}
        for m in (s.llm.agent_model, s.llm.embedding_model):
            check(f"ollama model {m}", m in names or f"{m}:latest" in names)
    except Exception as e:
        check("ollama reachable", False, str(e))
    for name, ok, detail in checks:
        print(f"{'✓' if ok else '✗'} {name}{'' if ok else f'  → {detail}'}")
    sys.exit(0 if all(ok for _, ok, _ in checks) else 1)


def cmd_serve(_: argparse.Namespace) -> None:
    from jarvis.main import run

    run()


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvisctl", description="J.A.R.V.I.S. Verwaltung")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Core-Server starten").set_defaults(fn=cmd_serve)
    sub.add_parser("migrate", help="Datenbank-Migrationen ausführen").set_defaults(fn=cmd_migrate)
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
    pr.add_argument("--role", default="operator", choices=["operator", "viewer"])
    pr.set_defaults(fn=cmd_pair)
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
