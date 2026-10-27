"""One release build: check out the commit, ``flutter build apk``, verify the APK.

The build runs in its own detached worktree of the repository (``<data_dir>/app-updates/checkout``), never in
Sir's checkout: open changes there are not built in, and the build directory is reused between builds (faster
incremental builds, the sherpa-onnx AAR is only downloaded once). Every step is an argv list without a shell;
``flutter`` comes from the shell allowlist (``toolchain.resolve_flutter``).

Verification before anything is stored (``inspect``): apksigner must verify the signature and report exactly the
release key's certificate, aapt2 must report the expected package, versionCode and versionName. A build that
fails any of these is rejected and never offered to a phone.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from jarvis.mobile.ota.signing import SigningKey, normalize_fingerprint
from jarvis.mobile.ota.toolchain import Runner, Toolchain

APP_SUBDIR = Path("apps/jarvis_mobile")
APPLICATION_ID = "systems.pje.jarvis"
APK_OUTPUT = Path("build/app/outputs/flutter-apk/app-release.apk")
ABIS = {"android-arm64": "arm64-v8a", "android-arm": "armeabi-v7a"}

_BADGING_PKG = re.compile(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", re.MULTILINE)
_BADGING_MIN = re.compile(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", re.MULTILINE)
_BADGING_TARGET = re.compile(r"^targetSdkVersion:'(\d+)'", re.MULTILINE)
_CERT = re.compile(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F:]+)\s*$", re.MULTILINE)
_SCHEME = re.compile(r"^Verified using v(\d)(?:\.\d)? scheme[^:]*: (true|false)", re.MULTILINE)


class BuildError(RuntimeError):
    def __init__(self, stage: str, message: str, cause: str = "build") -> None:
        super().__init__(message)
        self.stage = stage
        self.cause = cause


@dataclass(slots=True)
class ApkInfo:
    package: str
    version_code: int
    version_name: str
    min_sdk: int | None
    target_sdk: int | None
    signers: list[str]


def parse_badging(text: str) -> tuple[str, int, str, int | None, int | None]:
    m = _BADGING_PKG.search(text)
    if not m:
        raise BuildError("verify", "aapt2 lieferte keine Paketangaben")
    mn, tg = _BADGING_MIN.search(text), _BADGING_TARGET.search(text)
    return (
        m.group(1),
        int(m.group(2)),
        m.group(3),
        int(mn.group(1)) if mn else None,
        int(tg.group(1)) if tg else None,
    )


def parse_signers(text: str) -> list[str]:
    """Certificate fingerprints from ``apksigner verify --print-certs -v``; requires a v2+ signature (the scheme
    Android 7+ checks; v1/JAR alone is not accepted here)."""
    schemes = {int(v) for v, ok in _SCHEME.findall(text) if ok == "true"}
    if not schemes & {2, 3, 4}:
        raise BuildError("verify", "APK ist nicht mit dem APK-Signaturschema v2/v3 signiert")
    return [normalize_fingerprint(fp) for fp in _CERT.findall(text)]


class ApkBuilder:
    def __init__(self, repo: Path, checkout: Path, runner: Runner, log: Path) -> None:
        self.repo = repo
        self.checkout = checkout
        self.runner = runner
        self.log = log

    @property
    def app_dir(self) -> Path:
        return self.checkout / APP_SUBDIR

    async def _git(self, *args: str, cwd: Path | None = None, timeout_s: float = 300) -> str:
        code, out = await self.runner.run(
            ["git", "-C", str(cwd or self.repo), *args], timeout_s=timeout_s, log=self.log
        )
        if code != 0:
            raise BuildError("checkout", f"git {args[0]} fehlgeschlagen: {out.strip()[-300:]}")
        return out

    async def prepare(self, commit: str) -> None:
        """Detached worktree at ``commit``. A broken worktree is recreated; ignored files (build/, local.properties,
        the downloaded AAR) stay, untracked leftovers of an earlier commit are removed."""
        if not (self.checkout / ".git").exists():
            shutil.rmtree(self.checkout, ignore_errors=True)
            await self._git("worktree", "prune")
            self.checkout.parent.mkdir(parents=True, exist_ok=True)
            await self._git("worktree", "add", "--detach", "--force", str(self.checkout), commit)
        else:
            await self._git("checkout", "--force", "--detach", commit, cwd=self.checkout)
            await self._git("clean", "-fdq", cwd=self.checkout)
        head = (await self._git("rev-parse", "HEAD", cwd=self.checkout)).strip().splitlines()[-1]
        if head != commit:
            raise BuildError("checkout", f"Checkout steht auf {head[:12]} statt {commit[:12]}")

    async def build(
        self, tc: Toolchain, key: SigningKey, *, version_code: int, version_name: str, target: str, timeout_s: float
    ) -> Path:
        apk = self.app_dir / APK_OUTPUT
        apk.unlink(missing_ok=True)  # never pick up the APK of an earlier build
        env = {
            **tc.env,
            **key.gradle_env(),
            "JARVIS_ANDROID_ABIS": ABIS[target],
            # one-off Gradle daemon: the 8 GB heap of gradle.properties is released after the build
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
            "CI": "true",  # no interactive Flutter prompts / analytics banner
        }
        argv = [
            tc.flutter, "build", "apk", "--release", "--target-platform", target,
            "--build-number", str(version_code), "--build-name", version_name,
        ]  # fmt: skip
        code, out = await self.runner.run(
            argv, cwd=self.app_dir, env=env, timeout_s=timeout_s, log=self.log, redact=[key.password]
        )
        if code is None:
            raise BuildError("build", f"flutter build hat das Zeitlimit von {timeout_s / 60:g} Minuten überschritten")
        if code != 0:
            raise BuildError("build", f"flutter build fehlgeschlagen (Exit {code}): {_last_error(out)}")
        if not apk.is_file():
            raise BuildError("build", f"flutter build meldet Erfolg, aber {APK_OUTPUT} fehlt")
        return apk

    async def inspect(self, tc: Toolchain, apk: Path) -> ApkInfo:
        code, badging = await self.runner.run([tc.aapt2, "dump", "badging", str(apk)], env=tc.env, timeout_s=120)
        if code != 0:
            raise BuildError("verify", f"aapt2 konnte die APK nicht lesen: {badging.strip()[-300:]}")
        package, vcode, vname, min_sdk, target_sdk = parse_badging(badging)
        code, certs = await self.runner.run(
            [tc.apksigner, "verify", "--print-certs", "-v", str(apk)], env=tc.env, timeout_s=180
        )
        if code != 0:
            raise BuildError("verify", f"Signaturprüfung fehlgeschlagen: {certs.strip()[-300:]}")
        return ApkInfo(package, vcode, vname, min_sdk, target_sdk, parse_signers(certs))


def check_apk(info: ApkInfo, *, version_code: int, version_name: str, fingerprint: str) -> None:
    """Rejects anything but exactly the expected app, version and signer."""
    if info.package != APPLICATION_ID:
        raise BuildError("verify", f"falsche applicationId {info.package!r} (erwartet {APPLICATION_ID})")
    if info.version_code != version_code:
        raise BuildError("verify", f"versionCode {info.version_code} statt {version_code}")
    if info.version_name != version_name:
        raise BuildError("verify", f"versionName {info.version_name!r} statt {version_name!r}")
    if info.signers != [normalize_fingerprint(fingerprint)]:
        raise BuildError(
            "verify",
            "APK ist nicht (nur) mit dem Release-Schlüssel signiert – z. B. mit dem Debug-Schlüssel, weil die "
            "Signierdaten in Gradle nicht ankamen",
            cause="signature",
        )


def _last_error(out: str) -> str:
    """The most telling lines of a failed Flutter/Gradle build."""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    hits = [ln for ln in lines if re.search(r"error|FAILURE|What went wrong|Exception|failed", ln, re.IGNORECASE)]
    return " | ".join((hits or lines)[-4:])[-500:] or "keine Ausgabe"
