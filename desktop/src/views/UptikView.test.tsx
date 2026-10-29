// @vitest-environment jsdom
// UpTik › Veröffentlichen: the video source „Upload“ lists the Core's uploads folder and hands the choice over as
// „upload:<name>“; the inbox source keeps sending „inbox:<id>“. The API is a mock – nothing is ever published.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UptikAccount, UptikPostBody, UptikStatus } from "../lib/uptik";

const account: UptikAccount = {
  id: "a1", platform: "tiktok", platform_label: "TikTok", label: "PJE", handle: "", runner: "local", device: "",
  status: "connected", autopost: false, circuit_state: "ok", circuit_code: null, circuit_reason: null, circuit_at: null,
  last_error: null, connected_at: null, last_used_at: null, session_saved: true, session_saved_at: null, login_running: false,
};

const status: UptikStatus = {
  enabled: true, active: true, kill_switch: false,
  platforms: [{ id: "tiktok", label: "TikTok" }],
  settings: {
    runner: "local", device: "", device_staging_dir: "", headless: false, max_attempts: 3, retry_base_s: 60, max_parallel: 1,
    handoff_enabled: false, handoff_publish_time: "", youtube_shorts_enabled: false, login_timeout_s: 600, video_dirs: [],
  },
  local_runner: { ok: true, reason: "" },
  accounts: [account], tripped: [], counts: {},
};

const inboxItem = {
  id: "i1", source: "social", source_ref: "v1", name: "daily.mp4", size: 2048, duration: 12, title: "Tagesvideo",
  captions: {}, created_at: "2026-10-29T08:00:00Z",
};

const { api } = vi.hoisted(() => ({
  api: {
    uptik: {
      status: vi.fn(),
      inbox: vi.fn(),
      uploads: vi.fn(),
      upload: vi.fn(),
      validate: vi.fn(),
      publish: vi.fn(),
    },
  },
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: vi.fn(), token: "t", versions: {} as Record<string, number> }));
  return { useApp, api };
});
vi.mock("../lib/native", () => ({ openExternal: vi.fn() }));

import { UptikView } from "./UptikView";

/** The body of the first „Prüfen“ request. */
const sent = (): UptikPostBody => {
  const body = api.uptik.validate.mock.calls[0]?.[0] as UptikPostBody | undefined;
  if (!body) throw new Error("validate was not called");
  return body;
};

async function openPublish() {
  api.uptik.status.mockResolvedValue(status);
  api.uptik.inbox.mockResolvedValue([inboxItem]);
  api.uptik.uploads.mockResolvedValue([
    { name: "3f9a01bc-Messe Clip.mp4", size: 4096, modified_at: "2026-10-29T09:00:00Z" },
    { name: "0a1b2c3d-Alt.mov", size: 1024, modified_at: "2026-10-28T09:00:00Z" },
  ]);
  api.uptik.validate.mockResolvedValue({ ok: true, targets: [{ account_id: "a1", platform: "tiktok", label: "PJE" }] });
  render(<UptikView />);
  fireEvent.click(await screen.findByRole("tab", { name: "Veröffentlichen" }));
  fireEvent.click(await screen.findByRole("switch", { name: "TikTok PJE" }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("UptikView › Videoquelle", () => {
  it("offers „Upload“ and sends the chosen file as upload:<name>", async () => {
    await openPublish();
    expect(api.uptik.uploads).not.toHaveBeenCalled(); // only fetched once the source is chosen
    fireEvent.change(screen.getByLabelText("Quelle"), { target: { value: "upload" } });
    const select = await screen.findByLabelText("Aus dem Upload-Ordner");
    expect(screen.getByRole("option", { name: /Messe Clip · / })).toBeTruthy();
    fireEvent.change(select, { target: { value: "3f9a01bc-Messe Clip.mp4" } });
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await waitFor(() => expect(api.uptik.validate).toHaveBeenCalledTimes(1));
    expect(sent()).toMatchObject({ video: "upload:3f9a01bc-Messe Clip.mp4", accounts: ["a1"] });
  });

  it("keeps the inbox source as inbox:<id> and clears the choice when switching", async () => {
    await openPublish();
    fireEvent.change(await screen.findByLabelText("Aus dem Eingang (Videoproduktion und Uploads)"), { target: { value: "i1" } });
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await waitFor(() => expect(api.uptik.validate).toHaveBeenCalledTimes(1));
    expect(sent().video).toBe("inbox:i1");

    fireEvent.change(screen.getByLabelText("Quelle"), { target: { value: "upload" } });
    expect((await screen.findByLabelText<HTMLSelectElement>("Aus dem Upload-Ordner")).value).toBe("");
    expect((screen.getByRole("button", { name: "Prüfen" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("selects a freshly uploaded file as upload:<name> in the Upload source", async () => {
    await openPublish();
    api.uptik.upload.mockResolvedValue({ ...inboxItem, id: "i2", source: "upload", name: "77aa88bb-Neu.mp4", title: "Neu" });
    fireEvent.change(screen.getByLabelText("Quelle"), { target: { value: "upload" } });
    await screen.findByLabelText("Aus dem Upload-Ordner");
    const file = new File([new Uint8Array(16)], "Neu.mp4", { type: "video/mp4" });
    fireEvent.change(screen.getByLabelText("Videodatei"), { target: { files: [file] } });
    await waitFor(() => expect(api.uptik.upload).toHaveBeenCalledWith(file, "Neu.mp4"));
    await waitFor(() => expect(api.uptik.uploads).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await waitFor(() => expect(api.uptik.validate).toHaveBeenCalledTimes(1));
    expect(sent().video).toBe("upload:77aa88bb-Neu.mp4");
  });
});
