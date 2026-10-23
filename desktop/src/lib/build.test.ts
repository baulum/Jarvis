// @vitest-environment jsdom
// A desktop app older than the UI code the core runs from says so (once, and it stays until closed).
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, checkDesktopBuild, useApp } from "../store/app";
import { createApi } from "./api";
import { BUILD_COMMIT, staleBuildNotice, type DesktopBuild } from "./build";

const build = (over: Partial<DesktopBuild> = {}): DesktopBuild => ({
  build_commit: "0123456789ab",
  core_commit: "fedcba987654",
  stale: true,
  changes: 3,
  ...over,
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  useApp.setState({ toasts: [] });
});

describe("staleBuildNotice", () => {
  it("names the build, the number of UI changes and how to update", () => {
    const n = staleBuildNotice(build());
    expect(n?.title).toBe("Desktop-App ist veraltet");
    expect(n?.body).toContain("(0123456)");
    expect(n?.body).toContain("3 Änderungen");
    expect(n?.body).toContain("scripts/install-app.sh");
    expect(staleBuildNotice(build({ changes: 1 }))?.body).toContain("eine Änderung");
  });

  it("stays quiet when the app is current or the core cannot tell", () => {
    expect(staleBuildNotice(build({ stale: false, changes: 0 }))).toBeNull();
    expect(staleBuildNotice(build({ stale: null, reason: "kein git" }))).toBeNull();
  });

  it("the build commit is written in at build time", () => {
    expect(BUILD_COMMIT).toMatch(/^([0-9a-f]{40})?$/);
  });
});

describe("checkDesktopBuild", () => {
  it("asks the core with the build commit and shows a toast that does not time out", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(api, "desktopBuild").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(build());
    await checkDesktopBuild("0123456789ab"); // core not reachable: nothing shown, asked again later
    expect(useApp.getState().toasts).toHaveLength(0);
    await checkDesktopBuild("0123456789ab");
    expect(spy).toHaveBeenCalledWith("0123456789ab");
    const [toast] = useApp.getState().toasts;
    expect(toast).toMatchObject({ tone: "warn", title: "Desktop-App ist veraltet", sticky: true });
    vi.advanceTimersByTime(60_000);
    expect(useApp.getState().toasts).toHaveLength(1);

    await checkDesktopBuild("0123456789ab"); // a reconnect does not repeat it
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("the api passes the commit as query parameter", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(build()), { status: 200 }));
    const client = createApi({ baseUrl: "http://core", getToken: () => "tok", fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(await client.desktopBuild("abc1234")).toEqual(build());
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toBe("http://core/api/system/desktop-build?commit=abc1234");
  });
});
