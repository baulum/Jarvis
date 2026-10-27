"""OTA updates of the Android app: change detection, versioning, signing, release store, build pipeline, API."""

import asyncio
import hashlib
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.api.deps import current_principal
from jarvis.config import AppUpdateCfg
from jarvis.mobile.ota import api as ota_api
from jarvis.mobile.ota.builder import (
    APPLICATION_ID,
    ApkBuilder,
    ApkInfo,
    BuildError,
    check_apk,
    parse_badging,
    parse_signers,
)
from jarvis.mobile.ota.changes import affects_app, app_files
from jarvis.mobile.ota.releases import Release, ReleaseError, ReleaseStore, now_iso, sha256_file
from jarvis.mobile.ota.service import AppUpdateService, BuildRequest
from jarvis.mobile.ota.signing import KEYSTORE, SigningError, SigningKey, SigningManager
from jarvis.mobile.ota.toolchain import (
    Runner,
    Toolchain,
    ToolchainError,
    find_android_sdk,
    find_build_tools,
    resolve_flutter,
)
from jarvis.mobile.ota.versioning import (
    FIRST_VERSION_CODE,
    VersionError,
    is_upgrade,
    next_version_code,
    parse_version_name,
    pubspec_version,
    version_name,
)
from jarvis.mobile.push import PushResult
from jarvis.security.auth import Principal

FP = "ab" * 32
PHONE = uuid.uuid4()


# ------------------------------------------------------------------ changes / versioning
def test_only_files_that_end_up_in_the_apk_need_a_build() -> None:
    files = [
        "apps/jarvis_mobile/lib/main.dart",
        "apps/jarvis_mobile/android/app/src/main/AndroidManifest.xml",
        "apps/jarvis_mobile/test/foo_test.dart",
        "apps/jarvis_mobile/android/app/src/test/kotlin/X.kt",
        "apps/jarvis_mobile/README.md",
        "core/src/jarvis/main.py",
        "docs/MOBILE.md",
    ]
    assert app_files(files) == files[:2]
    assert not affects_app(["core/x.py", "apps/jarvis_mobile/test/a.dart"])
    assert affects_app(["apps/jarvis_mobile/pubspec.yaml"])
    assert affects_app(None)  # unknown diff: better build once too often


def test_version_codes_only_grow() -> None:
    assert next_version_code(0) == FIRST_VERSION_CODE  # above every manual --split-per-abi build (2001)
    assert next_version_code(3004, [3001, 3002]) == 3005
    assert next_version_code(3000, [3010]) == 3011  # an existing release wins
    assert next_version_code(0, [], pubspec_build=5000) == 5001
    with pytest.raises(VersionError):
        next_version_code(2_100_000_000)
    assert is_upgrade(3002, 3001) and is_upgrade(3002, None)
    assert not is_upgrade(3002, 3002) and not is_upgrade(3001, 3002)


def test_version_names() -> None:
    assert pubspec_version("name: x\nversion: 1.2.3+7\n") == ("1.2.3", 7)
    assert pubspec_version("version: '2.0.0'\n") == ("2.0.0", 0)
    assert pubspec_version("name: x\n") == ("1.0.0", 0)
    name = version_name("1.2.3", 3001, "abcdef0123456789")
    assert name == "1.2.3+3001.abcdef0"
    assert parse_version_name(name) == ("1.2.3", 3001, "abcdef0")
    assert parse_version_name("1.0.0") is None
    with pytest.raises(VersionError):
        version_name("1.2", 1, "abcdef0")
    with pytest.raises(VersionError):
        version_name("1.2.3", 1, "not-a-sha")


# ------------------------------------------------------------------ APK verification
BADGING = """package: name='systems.pje.jarvis' versionCode='3001' versionName='1.0.0+3001.abcdef0' platformBuildVersionName='16'
minSdkVersion:'26'
targetSdkVersion:'36'
application-label:'J.A.R.V.I.S.'
"""
CERTS = f"""Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Number of signers: 1
Signer #1 certificate DN: CN=J.A.R.V.I.S. Mobile
Signer #1 certificate SHA-256 digest: {FP}
Signer #1 certificate SHA-1 digest: 00
"""


def test_badging_and_signers_are_parsed() -> None:
    assert parse_badging(BADGING) == (APPLICATION_ID, 3001, "1.0.0+3001.abcdef0", 26, 36)
    assert parse_signers(CERTS) == [FP]
    with pytest.raises(BuildError):
        parse_badging("nothing")
    with pytest.raises(BuildError):  # v1 (JAR) only is not accepted
        parse_signers(CERTS.replace("v2 scheme (APK Signature Scheme v2): true", "v2 scheme (APK Signature Scheme v2): false")
                      .replace("v3 scheme (APK Signature Scheme v3): true", "v3 scheme (APK Signature Scheme v3): false"))  # fmt: skip


async def test_inspect_reads_the_whole_tool_output(tmp_path: Path) -> None:
    """Regression: badging lists every permission after the package line – more than run()'s 4000-char tail."""
    long_badging = BADGING + "".join(f"uses-permission: name='android.permission.P{i:04d}'\n" for i in range(400))
    long_certs = CERTS + "WARNING: META-INF/x not protected by signature\n" * 200

    class ToolRunner(Runner):
        async def run(self, argv: Any, **kw: Any) -> tuple[int | None, str]:
            raise AssertionError("inspect must not use the tail-only run()")

        async def output(self, argv: Any, **kw: Any) -> tuple[int | None, bytes]:
            return 0, (long_badging if "badging" in argv else long_certs).encode()

    builder = ApkBuilder(tmp_path, tmp_path, ToolRunner(), tmp_path / "log")
    tc = Toolchain("flutter", tmp_path, tmp_path, None, "keytool", "")
    info = await builder.inspect(tc, tmp_path / "app.apk")
    assert (info.package, info.version_code, info.signers) == (APPLICATION_ID, 3001, [FP])


def test_only_the_exact_app_version_and_key_pass() -> None:
    ok = ApkInfo(APPLICATION_ID, 3001, "1.0.0+3001.abcdef0", 26, 36, [FP])
    check_apk(ok, version_code=3001, version_name="1.0.0+3001.abcdef0", fingerprint=FP.upper())
    bad = [
        ApkInfo("systems.pje.jarvis.debug", 3001, ok.version_name, 26, 36, [FP]),
        ApkInfo(APPLICATION_ID, 3000, ok.version_name, 26, 36, [FP]),
        ApkInfo(APPLICATION_ID, 3001, "1.0.0", 26, 36, [FP]),
        ApkInfo(APPLICATION_ID, 3001, ok.version_name, 26, 36, ["cd" * 32]),  # debug key
        ApkInfo(APPLICATION_ID, 3001, ok.version_name, 26, 36, [FP, "cd" * 32]),
    ]
    for info in bad:
        with pytest.raises(BuildError):
            check_apk(info, version_code=3001, version_name="1.0.0+3001.abcdef0", fingerprint=FP)


# ------------------------------------------------------------------ release store
def _release(code: int, apk: Path, commit: str = "a" * 40) -> Release:
    return Release(
        application_id=APPLICATION_ID, version_code=code, version_name=f"1.0.0+{code}.{commit[:7]}", commit=commit,
        sha256=sha256_file(apk), size=apk.stat().st_size, signing_cert_sha256=FP, file=f"jarvis-mobile-{code}.apk",
        created_at=now_iso(),
    )  # fmt: skip


def test_release_store_add_withdraw_prune(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "releases")
    apk = tmp_path / "app.apk"
    for code in (3001, 3002, 3003, 3004):
        apk.write_bytes(f"apk {code}".encode())
        store.add(apk, _release(code, apk))
    assert [r.version_code for r in store.all()] == [3004, 3003, 3002, 3001]
    assert store.latest().version_code == 3004  # type: ignore[union-attr]
    with pytest.raises(ReleaseError):
        store.add(apk, _release(3004, apk))

    store.set_withdrawn(3004, True, "kaputt")
    assert store.latest().version_code == 3003  # type: ignore[union-attr]
    assert store.get(3004).withdrawn_reason == "kaputt"  # type: ignore[union-attr]
    store.set_withdrawn(3004, False)
    assert store.latest().version_code == 3004  # type: ignore[union-attr]

    assert store.prune(2, protect={3001}) == [3002]  # 3001 still runs on a phone
    assert [r.version_code for r in store.all()] == [3004, 3003, 3001]
    public = store.get(3004).public()  # type: ignore[union-attr]
    assert public["download_path"] == "/api/mobile/app-update/releases/3004/apk"
    assert str(tmp_path) not in str(public)


def test_release_store_rejects_a_corrupt_copy_and_skips_broken_entries(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "releases")
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    rel = _release(3001, apk)
    rel.sha256 = "0" * 64
    with pytest.raises(ReleaseError):
        store.add(apk, rel)
    assert store.all() == []
    (tmp_path / "releases" / "3005").mkdir(parents=True)
    (tmp_path / "releases" / "3005" / "manifest.json").write_text("{broken")
    assert store.all() == []


# ------------------------------------------------------------------ signing
class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


class KeytoolRunner(Runner):
    """keytool stand-in: -genkeypair writes a file, -exportcert returns a DER blob derived from the password."""

    def __init__(self) -> None:
        self.argv: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    async def run(self, argv: Any, **kw: Any) -> tuple[int | None, str]:
        self.argv.append(list(argv))
        if "-genkeypair" in argv:
            Path(argv[argv.index("-keystore") + 1]).write_bytes(b"p12")
        return 0, ""

    async def output(
        self, argv: Any, *, env: dict[str, str] | None = None, timeout_s: float = 60
    ) -> tuple[int | None, bytes]:
        self.envs.append(env or {})
        return 0, b"DER:" + (env or {}).get("JARVIS_KEYSTORE_PASS", "").encode()


async def test_signing_key_is_created_once_and_never_replaced(tmp_path: Path) -> None:
    secrets_ = FakeSecrets()
    runner = KeytoolRunner()
    mgr = SigningManager(tmp_path / "sign", "android-release-keystore", runner=runner, store=secrets_)  # type: ignore[arg-type]
    key = await mgr.ensure("keytool", None)
    password = secrets_.values["android-release-keystore"]
    assert key.fingerprint == hashlib.sha256(b"DER:" + password.encode()).hexdigest()
    assert (tmp_path / "sign" / KEYSTORE).is_file()
    assert (tmp_path / "sign").stat().st_mode & 0o777 == 0o700
    assert mgr.info()["fingerprint"] == key.fingerprint
    # the password never appears in an argv (keytool reads it from the environment)
    assert all(password not in " ".join(a) for a in runner.argv)
    assert "JARVIS_SIGNING_STORE_PASSWORD" in key.gradle_env() and password not in repr(key)

    again = await mgr.ensure("keytool", key.fingerprint)
    assert again.fingerprint == key.fingerprint
    assert sum("-genkeypair" in a for a in runner.argv) == 1

    with pytest.raises(SigningError) as e:
        await mgr.ensure("keytool", "cd" * 32)  # releases were signed with another key
    assert e.value.cause == "keystore_mismatch"

    (tmp_path / "sign" / KEYSTORE).unlink()
    with pytest.raises(SigningError) as e:  # never silently a new key once releases exist
        await mgr.ensure("keytool", key.fingerprint)
    assert e.value.cause == "keystore_missing"


async def test_signing_without_password_in_keychain(tmp_path: Path) -> None:
    secrets_ = FakeSecrets()
    mgr = SigningManager(tmp_path / "sign", "k", runner=KeytoolRunner(), store=secrets_)  # type: ignore[arg-type]
    await mgr.ensure("keytool", None)
    secrets_.values.clear()
    with pytest.raises(SigningError) as e:
        await mgr.ensure("keytool", None)
    assert e.value.cause == "keystore_password_missing"


# ------------------------------------------------------------------ toolchain
def test_flutter_must_be_allowlisted_and_found(tmp_path: Path) -> None:
    bin_dir = tmp_path / "flutter" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "flutter"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    cfg = AppUpdateCfg()
    assert resolve_flutter(cfg, ["git", "flutter"], str(bin_dir)) == str(exe)
    with pytest.raises(ToolchainError) as e:
        resolve_flutter(cfg, ["git"], str(bin_dir))
    assert e.value.cause == "flutter_not_allowed"
    with pytest.raises(ToolchainError) as e:
        resolve_flutter(AppUpdateCfg(flutter_command="/bin/sh"), ["flutter"], str(bin_dir))
    assert e.value.cause == "flutter_not_allowed"
    with pytest.raises(ToolchainError) as e:
        resolve_flutter(cfg, ["flutter"], str(tmp_path))
    assert e.value.cause == "flutter_missing"


def test_android_sdk_and_newest_build_tools_are_found(tmp_path: Path) -> None:
    sdk = tmp_path / "Library" / "Android" / "sdk"
    (sdk / "platforms").mkdir(parents=True)
    for v, complete in (("34.0.0", True), ("36.1.0", True), ("37.0.0-rc1", False)):
        d = sdk / "build-tools" / v
        d.mkdir(parents=True)
        (d / "aapt2").write_text("")
        if complete:
            (d / "apksigner").write_text("")
    assert find_android_sdk("", environ={}, home=tmp_path) == sdk
    assert find_android_sdk("", environ={"ANDROID_HOME": str(sdk)}, home=tmp_path / "x") == sdk
    assert find_android_sdk("", environ={}, home=tmp_path / "nothing") is None
    assert find_build_tools(sdk) == sdk / "build-tools" / "36.1.0"


# ------------------------------------------------------------------ service (real git, fake Flutter)
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, {"apps/jarvis_mobile/pubspec.yaml": "name: jarvis_mobile\nversion: 1.0.0+1\n",
                "apps/jarvis_mobile/lib/main.dart": "void main() {}\n", "core/x.py": "x = 1\n"}, "Start")  # fmt: skip
    return r


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def notify(self, event: str, title: str, body: str = "", **kw: Any) -> list[str]:
        self.sent.append((event, title, kw.get("data") or {}))
        return ["desktop"]


class FakePush:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send_all(self, kind: str, payload: dict[str, Any], **kw: Any) -> list[PushResult]:
        self.sent.append((kind, payload))
        return [PushResult(str(PHONE), True, None)]


def _service(tmp_path: Path, repo: Path, **cfg: Any) -> AppUpdateService:
    settings = SimpleNamespace(
        mobile=SimpleNamespace(app_update=AppUpdateCfg(**{"fetch": False, "debounce_s": 0, "min_free_gb": 0, **cfg})),
        paths=SimpleNamespace(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"),
        coding=SimpleNamespace(extra_path=[]),
    )
    (tmp_path / "data").mkdir(exist_ok=True)
    app = SimpleNamespace(
        settings=settings, push=FakePush(), notifier=FakeNotifier(), kill_switch=SimpleNamespace(engaged=False)
    )
    svc = AppUpdateService(app, repo=repo)
    tc = Toolchain("flutter", tmp_path, tmp_path, None, "keytool", "")

    async def toolchain() -> Toolchain:
        return tc

    async def ensure(keytool: str, expected: str | None) -> SigningKey:
        return SigningKey(tmp_path / "release.p12", "jarvis-release", FP, "pw")

    svc._toolchain = toolchain  # type: ignore[method-assign]
    svc.signing.ensure = ensure  # type: ignore[method-assign]
    svc.built = []  # type: ignore[attr-defined]
    svc.fail_with = None  # type: ignore[attr-defined]

    async def build(tc: Toolchain, key: SigningKey, *, version_code: int, version_name: str, **kw: Any) -> Path:
        if svc.fail_with is not None:  # type: ignore[attr-defined]
            raise svc.fail_with  # type: ignore[attr-defined]
        main_dart = (svc.builder.app_dir / "lib" / "main.dart").read_text()
        apk = tmp_path / "out.apk"
        apk.write_bytes(f"{version_code}:{main_dart}".encode())
        svc.built.append((version_code, version_name))  # type: ignore[attr-defined]
        return apk

    async def inspect(tc: Toolchain, apk: Path) -> ApkInfo:
        code, name = svc.built[-1]  # type: ignore[attr-defined]
        return ApkInfo(APPLICATION_ID, code, name, 26, 36, [FP])

    svc.builder.build = build  # type: ignore[method-assign]
    svc.builder.inspect = inspect  # type: ignore[method-assign]
    return svc


async def test_first_build_signs_versions_stores_and_distributes(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    head = _git(repo, "rev-parse", "HEAD")
    await svc.run(BuildRequest("Test"))
    rel = svc.releases.latest()
    assert rel is not None and rel.version_code == FIRST_VERSION_CODE and rel.commit == head
    assert rel.version_name == f"1.0.0+{FIRST_VERSION_CODE}.{head[:7]}"
    assert rel.sha256 == sha256_file(svc.releases.apk_path(rel)) and rel.signing_cert_sha256 == FP
    assert [c["subject"] for c in rel.changelog] == ["Start"]
    assert svc.state["build"]["state"] == "distributed" and svc.state["build"]["push_sent"] == 1
    assert svc.app.push.sent == [("app_update", {"version_code": rel.version_code, "version_name": rel.version_name})]
    assert svc.app.notifier.sent[-1][0] == "app_update.ready"
    # the build ran in its own worktree, Sir's checkout stays untouched
    assert (svc.builder.checkout / "apps/jarvis_mobile/lib/main.dart").is_file()
    assert _git(repo, "status", "--porcelain") == ""

    # nothing changed → no second build; core-only change → none either
    await svc.run(BuildRequest("Test"))
    assert len(svc.built) == 1  # type: ignore[attr-defined]
    _commit(repo, {"core/x.py": "x = 2\n"}, "Core only")
    await svc.run(BuildRequest("Test"))
    assert len(svc.built) == 1  # type: ignore[attr-defined]
    assert "Keine Änderung an der App" in svc.state["last_check"]["reason"]

    # an app change → a new, higher version from the new commit
    new = _commit(repo, {"apps/jarvis_mobile/lib/main.dart": "void main() { print(2); }\n"}, "App: neue Funktion")
    await svc.run(BuildRequest("Test"))
    latest = svc.releases.latest()
    assert latest is not None and latest.version_code == FIRST_VERSION_CODE + 1 and latest.commit == new
    assert [c["subject"] for c in latest.changelog] == ["App: neue Funktion"]
    assert b"print(2)" in svc.releases.apk_path(latest).read_bytes()
    # state survives a restart of the core
    assert AppUpdateService(svc.app, repo=repo).state["last_version_code"] == FIRST_VERSION_CODE + 1


async def test_failed_build_is_reported_and_not_repeated_for_the_same_commit(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    svc.fail_with = BuildError("build", "Gradle kaputt")  # type: ignore[attr-defined]
    await svc.run(BuildRequest("Test"))
    assert svc.state["build"]["state"] == "failed" and svc.state["build"]["message"] == "Gradle kaputt"
    assert svc.releases.all() == []
    assert svc.app.notifier.sent[-1][0] == "app_update.failed"
    assert svc.state["history"][0]["state"] == "failed"

    # first release exists after a fix; then a broken app commit fails and is not retried automatically
    svc.fail_with = None  # type: ignore[attr-defined]
    await svc.run(BuildRequest("Test", force=True))
    assert svc.releases.latest() is not None
    _commit(repo, {"apps/jarvis_mobile/lib/main.dart": "broken\n"}, "App kaputt")
    svc.fail_with = BuildError("build", "Dart-Fehler")  # type: ignore[attr-defined]
    await svc.run(BuildRequest("Test"))
    builds = len(svc.built)  # type: ignore[attr-defined]
    svc.fail_with = None  # type: ignore[attr-defined]
    await svc.run(BuildRequest("Test"))
    assert len(svc.built) == builds  # type: ignore[attr-defined]
    assert "Wiederholen" in svc.state["last_check"]["reason"]
    _commit(repo, {"apps/jarvis_mobile/lib/main.dart": "fixed\n"}, "App repariert")
    await svc.run(BuildRequest("Test"))
    assert len(svc.built) == builds + 1  # type: ignore[attr-defined]


async def test_missing_tool_is_retried_by_itself(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    await svc.run(BuildRequest("Test"))
    _commit(repo, {"apps/jarvis_mobile/lib/main.dart": "v2\n"}, "App v2")

    async def no_flutter() -> Toolchain:
        raise ToolchainError("flutter_missing", "Flutter fehlt")

    working = svc._toolchain
    svc._toolchain = no_flutter  # type: ignore[method-assign]
    await svc.run(BuildRequest("Test"))
    assert svc.state["build"]["cause"] == "flutter_missing"
    await svc.run(BuildRequest("Test"))  # the automatic retry fails the same way: no second notification
    assert [e for e, _, _ in svc.app.notifier.sent].count("app_update.failed") == 1
    svc._toolchain = working  # type: ignore[method-assign]
    due, why = await svc.needs_build(_git(repo, "rev-parse", "HEAD"))
    assert due and "Voraussetzung" in why
    await svc.run(BuildRequest("Test"))
    assert svc.state["build"]["state"] == "distributed" and svc.state["last_failure"] is None


async def test_rebuild_of_an_old_release_gets_a_higher_version(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    await svc.run(BuildRequest("Test"))
    first = svc.releases.latest()
    assert first is not None
    _commit(repo, {"apps/jarvis_mobile/lib/main.dart": "v2\n"}, "App v2")
    await svc.run(BuildRequest("Test"))
    svc.withdraw(first.version_code + 1, True, "stürzt ab")
    req = BuildRequest("Wiederherstellung", force=True, commit=first.commit, rebuild_of=first.version_code)
    await svc.run(req)
    latest = svc.releases.latest()
    assert latest is not None and latest.version_code == first.version_code + 2
    assert latest.commit == first.commit and latest.rebuild_of == first.version_code
    assert "Wiederherstellung" in latest.changelog[0]["subject"]


async def test_kill_switch_stops_builds(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    svc.app.kill_switch.engaged = True
    await svc.run(BuildRequest("Test", force=True))
    assert svc.built == [] and svc.state["last_check"]["reason"] == "Not-Aus aktiv"  # type: ignore[attr-defined]


async def test_interrupted_build_is_marked_after_restart(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    svc.state["build"] = {"state": "building", "commit": "a" * 40}
    svc._save()
    again = AppUpdateService(svc.app, repo=repo)
    assert again.state["build"]["state"] == "failed" and again.state["build"]["cause"] == "interrupted"


async def test_worker_debounces_requests_into_one_check(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo, auto_build=False)
    runs: list[str] = []

    async def run(req: BuildRequest) -> None:
        runs.append(req.reason)

    svc.run = run  # type: ignore[method-assign]
    svc.start()
    try:
        svc.request(BuildRequest("a"), debounce=0.05)
        svc.request(BuildRequest("b"), debounce=0.05)
        svc.request(BuildRequest("manuell", force=True))
        svc.request(BuildRequest("auto"), debounce=0.05)  # never replaces a waiting manual request
        for _ in range(50):
            if runs:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
    finally:
        await svc.stop()
    assert runs == ["manuell"]


def test_phone_reports_and_install_links(tmp_path: Path, repo: Path) -> None:
    svc = _service(tmp_path, repo)
    svc.state["devices"] = {}
    svc._publish = lambda: asyncio.sleep(0)  # type: ignore[method-assign,assignment]
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_report(svc))
    finally:
        loop.close()
    dev = svc.state["devices"][str(PHONE)]
    assert dev["state"] == "installed" and dev["version_code"] == 3001 and dev["name"] == "Pixel"

    token, expires = svc.create_install_link()
    assert expires > time.time()
    assert svc.use_install_link(token, count=True)
    assert not svc.use_install_link("wrong", count=False)
    svc._links[next(iter(svc._links))].expires = time.time() - 1
    assert not svc.use_install_link(token, count=False)


async def _report(svc: AppUpdateService) -> None:
    svc.record_report(str(PHONE), "Pixel", {"version_code": 3001, "state": "installed", "error": None})
    svc.record_report(str(uuid.uuid4()), "Alt", {"version_code": 1, "state": "bogus"})
    await asyncio.sleep(0)


# ------------------------------------------------------------------ API
OWNER = Principal(id="mac", role="owner", channel="desktop")
MOBILE = Principal(id="Pixel", role="mobile_owner", channel="mobile", device_id=PHONE)


@pytest.fixture
def api_env(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    async def no_audit(*a: Any, **kw: Any) -> None:
        pass

    async def paired() -> set[str]:
        return {str(PHONE)}

    monkeypatch.setattr(ota_api, "audit", no_audit)
    monkeypatch.setattr(ota_api, "_paired_ids", paired)
    monkeypatch.setattr(ota_api, "advertised_urls", lambda s: ["https://100.64.0.5:8443"])
    svc = _service(tmp_path, repo)
    asyncio.run(svc.run(BuildRequest("Test")))
    app = FastAPI()
    app.include_router(ota_api.router)
    app.state.jarvis = SimpleNamespace(app_update=svc, settings=svc.app.settings, gateway=None)
    who = {"p": MOBILE}
    app.dependency_overrides[current_principal] = lambda: who["p"]
    return SimpleNamespace(client=TestClient(app), svc=svc, who=who)


def test_latest_offers_only_a_higher_version(api_env: Any) -> None:
    c = api_env.client
    r = c.get("/api/mobile/app-update/latest", params={"current": 2001}).json()
    assert r["available"] and r["release"]["version_code"] == FIRST_VERSION_CODE
    assert r["release"]["signing_cert_sha256"] == FP
    r = c.get("/api/mobile/app-update/latest", params={"current": FIRST_VERSION_CODE}).json()
    assert not r["available"] and "aktuell" in r["reason"]


def test_apk_download_resumes_and_withdrawn_is_hidden(api_env: Any) -> None:
    c, svc = api_env.client, api_env.svc
    rel = svc.releases.latest()
    url = f"/api/mobile/app-update/releases/{rel.version_code}/apk"
    full = c.get(url)
    assert full.status_code == 200 and full.headers["x-jarvis-sha256"] == rel.sha256
    assert hashlib.sha256(full.content).hexdigest() == rel.sha256
    part = c.get(url, headers={"Range": "bytes=2-"})
    assert part.status_code == 206 and part.content == full.content[2:]
    assert part.headers["content-range"].startswith("bytes 2-")

    svc.withdraw(rel.version_code, True, "defekt")
    assert c.get(url).status_code == 410
    assert c.get("/api/mobile/app-update/latest", params={"current": 1}).json()["available"] is False
    api_env.who["p"] = OWNER
    assert c.get(url).status_code == 200


def test_reports_controls_and_roles(api_env: Any) -> None:
    c = api_env.client
    body = {"version_code": 2001, "version_name": "1.0.0", "state": "downloading", "target_code": 3000,
            "progress": 0.5, "can_install": True, "unattended": True, "sdk": 35}  # fmt: skip
    assert c.post("/api/mobile/app-update/report", json=body).json() == {"ok": True}
    assert c.post("/api/mobile/app-update/report", json={**body, "state": "hacked"}).status_code == 422
    assert c.post("/api/mobile/app-update/build").status_code == 403  # phones may check, not build
    assert c.post("/api/mobile/app-update/install-link").status_code == 403

    api_env.who["p"] = OWNER
    snap = c.get("/api/mobile/app-update/status").json()
    assert snap["latest"]["version_code"] == FIRST_VERSION_CODE
    assert [d["device_id"] for d in snap["devices"]] == [str(PHONE)]
    assert snap["devices"][0]["progress"] == 0.5
    assert c.post("/api/mobile/app-update/report", json=body).status_code == 403  # only phones report
    assert c.post("/api/mobile/app-update/releases/99/withdraw", json={}).status_code == 404


def test_install_link_for_the_first_installation(api_env: Any) -> None:
    c, svc = api_env.client, api_env.svc
    api_env.who["p"] = OWNER
    r = c.post("/api/mobile/app-update/install-link").json()
    assert r["urls"][0].startswith("https://100.64.0.5:8443/api/mobile/app-update/install/")
    assert r["svg"].startswith("<?xml") or "<svg" in r["svg"]
    path = r["urls"][0].removeprefix("https://100.64.0.5:8443")
    page = c.get(path)
    assert page.status_code == 200 and "APK herunterladen" in page.text
    assert svc.releases.latest().sha256 in page.text
    assert page.headers["cache-control"] == "no-store"
    apk = c.get(path + "/jarvis.apk")
    assert apk.status_code == 200 and apk.headers["content-type"] == "application/vnd.android.package-archive"
    assert c.get("/api/mobile/app-update/install/wrong-token").status_code == 404
