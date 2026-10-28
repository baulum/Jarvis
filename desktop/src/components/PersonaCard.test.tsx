// @vitest-environment jsdom
// Einstellungen → Persönlichkeit: switching between Jarvis and René Redo, following a switch from the phone, Hörprobe.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PersonaStatus } from "../lib/types";

const status = (active: string, reneReady = true): PersonaStatus => ({
  active,
  personas: [
    { id: "jarvis", label: "J.A.R.V.I.S.", description: "Britischer Butler", voice: "jarvis", sample: "", voice_ready: true },
    { id: "rene_redo", label: "René Redo", description: "Aufgedrehter Kumpel", voice: "rene_redo", sample: "Alter!", voice_ready: reneReady },
  ],
});

const { api, store } = vi.hoisted(() => ({
  api: {
    persona: vi.fn(),
    setPersona: vi.fn(),
    personaPreview: vi.fn(async () => new Blob(["RIFF"], { type: "audio/wav" })),
  },
  store: { current: null as unknown as { setState: (s: object) => void } },
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: vi.fn(), token: "t", versions: { system: 0 } as Record<string, number> }));
  store.current = useApp;
  return { useApp, api };
});
URL.createObjectURL = vi.fn(() => "blob:preview");
URL.revokeObjectURL = vi.fn();
const played: string[] = [];
class FakeAudio {
  onended: (() => void) | null = null;
  constructor(readonly src: string) {}
  play() {
    played.push(this.src);
    return Promise.resolve();
  }
  pause() {}
}
vi.stubGlobal("Audio", FakeAudio);

import { PersonaCard } from "./PersonaCard";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  played.length = 0;
});

describe("PersonaCard", () => {
  it("switches to René Redo and marks him active", async () => {
    api.persona.mockResolvedValue(status("jarvis"));
    api.setPersona.mockResolvedValue(status("rene_redo"));
    render(<PersonaCard />);
    const rene = await screen.findByRole("radio", { name: /René Redo/ });
    expect(screen.getByRole("radio", { name: /J\.A\.R\.V\.I\.S\./ }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(rene);
    await waitFor(() => expect(rene.getAttribute("aria-checked")).toBe("true"));
    expect(api.setPersona).toHaveBeenCalledWith("rene_redo");
  });

  it("follows a switch made on another device (persona.changed bumps the system topic)", async () => {
    api.persona.mockResolvedValueOnce(status("jarvis")).mockResolvedValueOnce(status("rene_redo"));
    render(<PersonaCard />);
    await screen.findByRole("radio", { name: /René Redo/ });
    act(() => store.current.setState({ versions: { system: 1 } }));
    await waitFor(() => expect(screen.getByRole("radio", { name: /René Redo/ }).getAttribute("aria-checked")).toBe("true"));
    expect(api.setPersona).not.toHaveBeenCalled();
  });

  it("plays a Hörprobe without switching and warns when the voice is not loaded", async () => {
    api.persona.mockResolvedValue(status("jarvis", false));
    render(<PersonaCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Hörprobe: René Redo" }));
    await waitFor(() => expect(played).toEqual(["blob:preview"]));
    expect(api.personaPreview).toHaveBeenCalledWith("rene_redo");
    expect(api.setPersona).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Hörprobe stoppen" })).toBeTruthy();
    expect(screen.getByText(/Eigene Stimme noch nicht geladen/)).toBeTruthy();
  });
});
