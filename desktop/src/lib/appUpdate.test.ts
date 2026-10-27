import { describe, expect, it } from "vitest";
import { buildActive, buildTone, deviceHint, deviceLine, deviceTone, shortFingerprint, summary } from "./appUpdate";
import type { AppUpdateDevice, AppUpdateStatus } from "./types";

const status = (over: Partial<AppUpdateStatus> = {}): AppUpdateStatus => ({
  enabled: true,
  auto_build: true,
  branch: "main",
  application_id: "systems.pje.jarvis",
  build: { state: "idle" },
  queued: false,
  queued_reason: null,
  busy: false,
  last_check: null,
  last_failure: null,
  latest: null,
  releases: [],
  history: [],
  devices: [],
  signing: { exists: false, fingerprint: null, created_at: null, location: "/x" },
  toolchain: { ok: null },
  keep_releases: 5,
  log_file: "/x/android-build.log",
  ...over,
});

const device = (over: Partial<AppUpdateDevice> = {}): AppUpdateDevice => ({
  device_id: "d1",
  name: "Pixel",
  state: "up_to_date",
  updated_at: "2026-10-27T10:00:00+00:00",
  version_code: 3001,
  version_name: "1.0.0+3001.abc1234",
  can_install: true,
  unattended: true,
  ...over,
});

describe("app update labels", () => {
  it("knows which build states are running", () => {
    expect(buildActive({ state: "building" })).toBe(true);
    expect(buildActive({ state: "distributed" })).toBe(false);
    expect(buildActive(undefined)).toBe(false);
    expect(buildTone({ state: "failed" })).toBe("bad");
    expect(buildTone({ state: "verifying" })).toBe("info");
    expect(buildTone({ state: "distributed" })).toBe("ok");
  });

  it("summarises the card", () => {
    expect(summary(status({ enabled: false }))).toContain("ausgeschaltet");
    expect(summary(status({ build: { state: "building", version_name: "1.0.0+3002.def5678", message: "flutter build apk" } })))
      .toBe("Baue 1.0.0+3002.def5678 – flutter build apk");
    expect(summary(status({ build: { state: "failed", message: "Gradle kaputt" } }))).toBe("Build fehlgeschlagen: Gradle kaputt");
    expect(summary(status({ queued: true, queued_reason: "Coding-Job abc abgeschlossen" }))).toContain("Coding-Job");
    expect(summary(status({ last_check: { at: "x", decision: "none", reason: "Keine Änderung an der App" } }))).toBe("Keine Änderung an der App");
  });

  it("describes a phone and what it still needs", () => {
    expect(deviceLine(device({ state: "downloading", target_code: 3002, progress: 0.42 }), 3002)).toBe("lädt 3002 · 42 %");
    expect(deviceLine(device(), 3002)).toContain("neuere Version");
    expect(deviceTone(device({ version_code: 3002 }), 3002)).toBe("ok");
    expect(deviceTone(device({ state: "failed" }), 3002)).toBe("bad");
    expect(deviceTone(device({ can_install: false }), 3002)).toBe("warn");
    expect(deviceHint(device({ can_install: false }))).toContain("Unbekannte Apps installieren");
    expect(deviceHint(device({ unattended: false }))).toContain("bestätigen");
    expect(deviceHint(device({ state: "unsupported", error: "anderer Signaturschlüssel" }))).toBe("anderer Signaturschlüssel");
    expect(deviceHint(device())).toBeNull();
  });

  it("formats certificate fingerprints like Android", () => {
    expect(shortFingerprint("abcdef0123456789abcdef", 4)).toBe("AB:CD:EF:01:…");
    expect(shortFingerprint(null)).toBe("–");
  });
});
