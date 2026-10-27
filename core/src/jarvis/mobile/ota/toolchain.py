"""Flutter, Android SDK and JDK for release builds – found without a shell profile (the core runs under launchd).

Nothing is installed or changed here. When something is missing, ``ToolchainError`` names what and where it was
looked for, so the update status can tell Sir exactly what to do.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import AppUpdateCfg

FLUTTER = "flutter"
OUTPUT_TAIL = 4000


class ToolchainError(RuntimeError):
    """Something needed for the build is missing; ``cause`` is a short machine-readable code."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(slots=True)
class Toolchain:
    flutter: str
    android_sdk: Path
    build_tools: Path
    java_home: Path | None
    keytool: str
    path: str  # PATH for every build step
    env: dict[str, str] = field(default_factory=dict)

    @property
    def apksigner(self) -> str:
        return str(self.build_tools / ("apksigner.bat" if sys.platform == "win32" else "apksigner"))

    @property
    def aapt2(self) -> str:
        return str(self.build_tools / ("aapt2.exe" if sys.platform == "win32" else "aapt2"))

    def summary(self) -> dict[str, str | None]:
        return {
            "flutter": self.flutter,
            "android_sdk": str(self.android_sdk),
            "build_tools": self.build_tools.name,
            "java_home": str(self.java_home) if self.java_home else None,
        }


# ------------------------------------------------------------------ processes
class Runner:
    """Runs build commands as argv lists (never through a shell). Tests replace it."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 60,
        log: Path | None = None,
        redact: Iterable[str] = (),
    ) -> tuple[int | None, str]:
        """(exit code, tail of the output); exit code None = timeout. The output goes line by line into ``log``
        with every string in ``redact`` replaced (passwords never reach a log file)."""
        secrets = [s for s in redact if s]

        def clean(text: str) -> str:
            for s in secrets:
                text = text.replace(s, "***")
            return text

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            return 127, clean(f"{argv[0]}: {e}")
        tail = ""
        out = log.open("a", encoding="utf-8") if log else None
        try:

            async def pump() -> None:
                nonlocal tail
                assert proc.stdout is not None
                async for raw in proc.stdout:
                    line = clean(raw.decode(errors="replace"))
                    tail = (tail + line)[-OUTPUT_TAIL:]
                    if out:
                        out.write(line)
                        out.flush()

            await asyncio.wait_for(pump(), timeout_s)
            await proc.wait()
        except TimeoutError:
            await _kill(proc)
            return None, tail + f"\nTimeout nach {timeout_s / 60:g} Minuten"
        except asyncio.CancelledError:  # core stops: the build (and Gradle) must not outlive it
            await _kill(proc)
            raise
        finally:
            if out:
                out.close()
        return proc.returncode, tail

    async def output(
        self, argv: Sequence[str], *, env: dict[str, str] | None = None, timeout_s: float = 60
    ) -> tuple[int | None, bytes]:
        """Raw stdout (e.g. a DER certificate); stderr is dropped."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return 127, b""
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return None, b""
        return proc.returncode, out


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Ends the process and its children (own session: Flutter → Gradle → Kotlin daemon)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, 9)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(proc.wait()), 10)


# ------------------------------------------------------------------ detection
def search_path(extra: Iterable[str | Path] = (), environ: dict[str, str] | None = None) -> str:
    """PATH like the shell connector's: the core's PATH, ``coding.extra_path`` (Flutter SDK) and Homebrew."""
    env = os.environ if environ is None else environ
    parts = [env.get("PATH", ""), *[os.path.expanduser(str(p)) for p in extra], "/opt/homebrew/bin", "/usr/local/bin"]
    return os.pathsep.join(p for p in parts if p)


def find_android_sdk(configured: str, environ: dict[str, str] | None = None, home: Path | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    home = home or Path.home()
    candidates = [
        configured,
        env.get("ANDROID_HOME", ""),
        env.get("ANDROID_SDK_ROOT", ""),
        str(home / "Library" / "Android" / "sdk"),  # macOS (Android Studio default)
        str(home / "Android" / "Sdk"),  # Linux
        os.path.join(env.get("LOCALAPPDATA", ""), "Android", "Sdk") if env.get("LOCALAPPDATA") else "",
    ]
    for c in candidates:
        if c and (p := Path(os.path.expanduser(c))).is_dir() and (p / "platforms").is_dir():
            return p
    return None


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(x) if x.isdigit() else -1 for x in name.replace("-", ".").split("."))


def find_build_tools(sdk: Path) -> Path | None:
    """Newest ``build-tools/<version>`` that has both apksigner and aapt2."""
    root = sdk / "build-tools"
    if not root.is_dir():
        return None
    exe = ".bat" if sys.platform == "win32" else ""
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / f"apksigner{exe}").exists() and any(d.glob("aapt2*"))]
    return max(dirs, key=lambda d: _version_key(d.name), default=None)


def _java_ok(home: Path) -> bool:
    return (home / "bin" / ("java.exe" if sys.platform == "win32" else "java")).exists()


async def find_java_home(
    configured: str, runner: Runner, environ: dict[str, str] | None = None, home: Path | None = None
) -> Path | None:
    """The JDK Flutter uses by default comes with Android Studio; else JAVA_HOME or macOS' java_home."""
    env = os.environ if environ is None else environ
    home = home or Path.home()
    candidates = [
        configured,
        "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
        str(home / "Applications" / "Android Studio.app" / "Contents" / "jbr" / "Contents" / "Home"),
        str(home / "android-studio" / "jbr"),
        "/opt/android-studio/jbr",
        env.get("JAVA_HOME", ""),
    ]
    for c in candidates:
        if c and _java_ok(p := Path(os.path.expanduser(c))):
            return p
    if Path("/usr/libexec/java_home").exists():
        code, out = await runner.output(["/usr/libexec/java_home"], timeout_s=10)
        if code == 0 and out.strip() and _java_ok(p := Path(out.decode().strip())):
            return p
    return None


def resolve_flutter(cfg: AppUpdateCfg, allowlist: Iterable[str], path: str) -> str:
    """The Flutter executable, only if ``flutter`` is in the shell allowlist (permissions.yaml)."""
    cmd = cfg.flutter_command.strip() or FLUTTER
    if Path(cmd).name.removesuffix(".bat") != FLUTTER:
        raise ToolchainError(
            "flutter_not_allowed", f"mobile.app_update.flutter_command muss 'flutter' sein, nicht {cmd!r}"
        )
    if FLUTTER not in set(allowlist):
        raise ToolchainError(
            "flutter_not_allowed", "'flutter' steht nicht in der Shell-Allowlist (config/permissions.yaml)"
        )
    found = cmd if os.path.isabs(cmd) and os.access(cmd, os.X_OK) else shutil.which(cmd, path=path)
    if not found:
        raise ToolchainError(
            "flutter_missing",
            "Flutter-SDK nicht gefunden. Pfad zu flutter/bin in coding.extra_path eintragen "
            "(config/jarvis.yaml) oder mobile.app_update.flutter_command auf den vollen Pfad setzen.",
        )
    return found


async def detect(
    cfg: AppUpdateCfg,
    *,
    extra_path: Iterable[str | Path],
    allowlist: Iterable[str],
    runner: Runner,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Toolchain:
    env_in = dict(os.environ if environ is None else environ)
    path = search_path(extra_path, env_in)
    flutter = resolve_flutter(cfg, allowlist, path)
    sdk = find_android_sdk(cfg.android_sdk, env_in, home)
    if sdk is None:
        raise ToolchainError(
            "android_sdk_missing",
            "Android-SDK nicht gefunden (ANDROID_HOME, ~/Library/Android/sdk). Einmalig Android Studio installieren "
            "und das SDK einrichten oder mobile.app_update.android_sdk setzen.",
        )
    tools = find_build_tools(sdk)
    if tools is None:
        raise ToolchainError(
            "build_tools_missing",
            f"Android-Build-Tools mit apksigner/aapt2 fehlen in {sdk / 'build-tools'} – im SDK Manager "
            "„Android SDK Build-Tools“ installieren.",
        )
    java = await find_java_home(cfg.java_home, runner, env_in, home)
    if java is None:
        raise ToolchainError(
            "jdk_missing",
            "Kein JDK gefunden (Android Studio, JAVA_HOME, /usr/libexec/java_home) – Android Studio installieren "
            "oder mobile.app_update.java_home setzen.",
        )
    keytool = java / "bin" / ("keytool.exe" if sys.platform == "win32" else "keytool")
    if not keytool.exists():
        raise ToolchainError("jdk_missing", f"keytool fehlt im JDK {java}")
    run_path = os.pathsep.join([str(java / "bin"), path])
    env = {
        k: v
        for k, v in env_in.items()
        if not k.startswith(("JARVIS_SECRET", "AWS_", "GITHUB_TOKEN", "JARVIS_SIGNING_"))
    }
    env.update(PATH=run_path, ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk), JAVA_HOME=str(java))
    return Toolchain(
        flutter=flutter,
        android_sdk=sdk,
        build_tools=tools,
        java_home=java,
        keytool=str(keytool),
        path=run_path,
        env=env,
    )
