// @vitest-environment jsdom
// Chat rendering: GFM Markdown (sanitised), interactive task lists, tool calls in event order, thought process.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { toggleCheck, pushToast, openExternal, copyText, media } = vi.hoisted(() => ({
  toggleCheck: vi.fn(async () => undefined),
  pushToast: vi.fn(),
  openExternal: vi.fn(async (_url: string) => undefined),
  copyText: vi.fn(async (_text: string) => true),
  media: vi.fn(async (_kind: string, file: string) => new Blob([file])),
}));
vi.mock("../../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ toggleCheck, pushToast }));
  return { useApp, api: { media } };
});
// jsdom has no object URLs
URL.createObjectURL = vi.fn((b: Blob) => `blob:test/${b.size}`);
URL.revokeObjectURL = vi.fn();
vi.mock("../../lib/native", () => ({ openExternal }));
vi.mock("../../lib/clipboard", () => ({ copyText }));
vi.mock("./RouteMap", () => ({
  default: ({ display }: { display: { routes: unknown[]; start: { label: string } } }) => (
    <div data-testid="route-map">
      {display.start.label} · {display.routes.length} Routen
    </div>
  ),
}));

import { Markdown } from "../Markdown";
import type { ChatThread, TimelineEvent } from "../../lib/timeline";
import { ChatTimeline } from "./ChatTimeline";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const item = (id: string, seq: number, kind: TimelineEvent["kind"], data: Record<string, unknown>, turn = "t1"): TimelineEvent =>
  ({ id, key: id, session_id: "s1", turn_id: turn, seq, ts: 1_700_000_000_000 + seq, kind, data }) as unknown as TimelineEvent;
const thread = (events: TimelineEvent[], running = false): ChatThread => ({ sessionId: "s1", events, running, hasMore: false, loaded: true });

describe("Markdown", () => {
  it("renders GFM: headings, tables, quotes, lists, code with highlighting", () => {
    const { container } = render(
      <Markdown
        text={"## Plan\n\n| Tag | Ort |\n|:--|--:|\n| Mo | Berlin |\n\n> Hinweis\n\n1. eins\n2. zwei\n\n- a\n\n```python\ndef f():\n    return 1\n```"}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Plan" })).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Tag", "Ort"]);
    expect(container.querySelector("blockquote")?.textContent).toContain("Hinweis");
    expect(container.querySelector("ol")?.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("ul")).toBeTruthy();
    const keyword = [...container.querySelectorAll("pre code span")].find((s) => s.textContent === "def");
    expect(keyword?.className).toContain("text-arc");
  });

  it("opens links externally and safely, never renders raw HTML or unsafe schemes", () => {
    const { container } = render(<Markdown text={'[Doku](https://example.com) <img src=x onerror="alert(1)"> [böse](javascript:alert(1))'} />);
    const link = screen.getByRole("link", { name: "Doku" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(screen.queryByRole("link", { name: "böse" })).toBeNull();
  });

  it("toggles task-list items by their index and shows the stored state", () => {
    const onToggle = vi.fn();
    render(<Markdown text={"- [ ] Schirm\n- [x] Kalender\n  - [ ] Unterpunkt"} checks={{ "0": true }} onToggleTask={onToggle} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((b) => b.getAttribute("aria-checked"))).toEqual(["true", "true", "false"]); // 0 from `checks`
    fireEvent.click(boxes[2] as HTMLElement);
    expect(onToggle).toHaveBeenCalledWith(2, true);
    fireEvent.click(boxes[0] as HTMLElement);
    expect(onToggle).toHaveBeenLastCalledWith(0, false);
  });

  it("renders bold, lists, tables and line breaks of an answer instead of raw markup", () => {
    const { container } = render(
      <Markdown text={"**Arbeit und Projekte**\nZweite Zeile\n\n- Kunde A\n- Kunde B\n\n| Projekt | Status |\n|---|---|\n| Website | offen |"} />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("Arbeit und Projekte");
    expect(container.querySelector("p br")).toBeTruthy();
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("tbody td")).toHaveLength(2);
    expect(container.textContent).not.toContain("*");
    expect(container.textContent).not.toContain("- Kunde");
  });
});

describe("ChatTimeline", () => {
  const events = [
    item("u", 1, "message", { role: "user", text: "Wie komme ich nach Potsdam?" }),
    item("turn", 2, "turn", { status: "done", reasoning: false, duration_ms: 4200 }),
    item("p1", 3, "process", { step: "memory", source: "protocol", title: "Gedächtnis durchsucht", text: "keine passenden Einträge" }),
    item("m1", 4, "message", { role: "assistant", text: "Ich berechne die Route." }),
    item("tool", 5, "tool", {
      name: "route_plan",
      tool_use_id: "toolu_1",
      status: "ok",
      started_ts: 1000,
      ended_ts: 1800,
      duration_ms: 800,
      input: '{\n  "start": "Berlin"\n}',
      result: '{\n  "routes": 2\n}',
      display: {
        kind: "route",
        mode: "auto",
        start: { label: "Berlin", lat: 52.5, lon: 13.4 },
        destination: { label: "Potsdam", lat: 52.4, lon: 13.06 },
        routes: [{ distance_m: 35200, duration_s: 2460, geometry: [[52.5, 13.4], [52.4, 13.06]] }],
        tiles: { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: "© OSM" },
      },
    }),
    item("p2", 6, "process", { step: "tool", source: "protocol", title: "Werkzeug: route_plan", status: "ok", ref: "tool" }),
    item("m2", 7, "message", { role: "assistant", text: "- [ ] Tanken\n- [ ] Losfahren" }),
    item("p3", 8, "process", { step: "summary", source: "summary", title: "Ablauf", text: "Dauer 4,2 s" }),
    item("sys", 9, "message", { role: "system", text: "Fehler: Dienst nicht erreichbar", level: "error" }),
  ];

  it("shows message types apart and text and tool calls in event order", async () => {
    const { container } = render(<ChatTimeline thread={thread(events)} />);
    const order = [...container.querySelectorAll(".timeline-row")].map((r) => r.textContent ?? "");
    expect(order[0]).toContain("Wie komme ich nach Potsdam?");
    expect(order[1]).toContain("Jarvis");
    expect(order[2]).toContain("Dienst nicht erreichbar");
    const answer = container.querySelectorAll(".timeline-row")[1] as HTMLElement;
    const flow = answer.textContent ?? "";
    expect(flow.indexOf("Ich berechne die Route.")).toBeLessThan(flow.indexOf("route_plan"));
    expect(flow.indexOf("route_plan")).toBeLessThan(flow.indexOf("Tanken"));
    expect(screen.getByRole("status").textContent).toContain("Fehler");
    expect(await screen.findByTestId("route-map")).toBeTruthy(); // the route as a map, not as text
  });

  it("renders Markdown in the user's own message and in answers, without raw syntax", () => {
    const md = [
      item("u", 1, "message", { role: "user", text: "**test** und *kursiv*" }),
      item("a", 2, "message", { role: "assistant", text: "**fett**" }),
    ];
    for (const compact of [false, true]) {
      const { container } = render(<ChatTimeline thread={thread(md)} compact={compact} />);
      expect([...container.querySelectorAll("strong")].map((s) => s.textContent)).toEqual(["test", "fett"]);
      expect(container.querySelector("em")?.textContent).toBe("kursiv");
      expect(container.textContent).not.toContain("*");
      cleanup();
    }
  });

  it("renders a Jarvis answer as HTML: bold, lists, table, heading, quote, code, strike, task list, link", () => {
    const text = [
      "## Tagesplan",
      "",
      "Heute ist **viel** los, ~~nichts~~ abgesagt:",
      "",
      "- **09:00** Standup",
      "- Kunde *Müller*",
      "  1. Agenda",
      "",
      "| Projekt | Status |",
      "| --- | :---: |",
      "| Website | **fertig** |",
      "",
      "> **Hinweis:** bestätigt",
      "",
      "- [x] E-Mails",
      "- [ ] Rechnung",
      "",
      "```ts",
      "const a = **1**;",
      "```",
      "",
      "Mehr unter [Doku](https://example.com/doku).",
    ].join("\n");
    for (const compact of [false, true]) {
      const { container } = render(<ChatTimeline thread={thread([item("a", 1, "message", { role: "assistant", text, status: "done" })])} compact={compact} />);
      expect(screen.getByRole("heading", { level: 2, name: "Tagesplan" })).toBeTruthy();
      expect([...container.querySelectorAll("p strong, li strong")].map((s) => s.textContent)).toEqual(["viel", "09:00", "Hinweis:"]);
      expect(container.querySelector("del")?.textContent).toBe("nichts");
      const list = container.querySelector("ul") as HTMLElement;
      expect([...list.children].map((li) => li.tagName)).toEqual(["LI", "LI"]);
      expect(list.querySelector("ol li")?.textContent).toBe("Agenda");
      const table = screen.getByRole("table");
      expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Projekt", "Status"]);
      expect(within(table).getByRole("cell", { name: "fertig" }).querySelector("strong")).toBeTruthy();
      expect(container.querySelector("blockquote strong")?.textContent).toBe("Hinweis:");
      expect(screen.getAllByRole("checkbox").map((b) => b.getAttribute("aria-checked"))).toEqual(["true", "false"]);
      expect(container.querySelector("pre code")?.textContent).toBe("const a = **1**;"); // code stays verbatim
      expect(screen.getByRole("link", { name: "Doku" }).getAttribute("href")).toBe("https://example.com/doku");
      // no Markdown syntax left outside the code block
      const prose = container.cloneNode(true) as HTMLElement;
      prose.querySelectorAll("pre").forEach((p) => p.remove());
      expect(prose.textContent).not.toMatch(/\*|#|~~|\||\[[ x]\]|```|^>/m);
      cleanup();
    }
  });

  it("opens links in the system browser, never in the app, and copies them if that fails", async () => {
    render(<ChatTimeline thread={thread([item("a", 1, "message", { role: "assistant", text: "[Doku](https://example.com/doku)" })])} />);
    const link = screen.getByRole("link", { name: "Doku" });
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true); // the webview itself does not navigate
    expect(openExternal).toHaveBeenCalledWith("https://example.com/doku");
    openExternal.mockRejectedValueOnce(new Error("blocked"));
    fireEvent.click(link);
    await vi.waitFor(() => expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ title: "Link kopiert" })));
    expect(copyText).toHaveBeenCalledWith("https://example.com/doku");
  });

  it("expands a tool call with input, result and timing", () => {
    render(<ChatTimeline thread={thread(events)} />);
    const row = screen.getByTitle("route_plan: erfolgreich");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(within(row).getByText("800 ms")).toBeTruthy();
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Eingabe")).toBeTruthy();
    expect(screen.getByText(/"start": "Berlin"/)).toBeTruthy();
    expect(screen.getByText(/"routes": 2/)).toBeTruthy();
    expect(screen.getByText("toolu_1")).toBeTruthy();
  });

  it("thought process: collapsed by default, sources labelled, honest hint without model thoughts", () => {
    render(<ChatTimeline thread={thread(events)} />);
    const toggle = screen.getByRole("button", { name: /Gedankenprozess/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Gedächtnis durchsucht")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText(/keine Gedanken übermittelt/)).toBeTruthy();
    expect(screen.getByText("Gedächtnis durchsucht")).toBeTruthy();
    expect(screen.getAllByText("Protokoll")).toHaveLength(2);
    expect(screen.getByText("Zusammenfassung")).toBeTruthy();
    expect(screen.queryByText("Modell")).toBeNull();
  });

  it("shows model reasoning with its source when the model sent it, live while running", () => {
    const running = [
      item("u", 1, "message", { role: "user", text: "Plane" }),
      item("turn", 2, "turn", { status: "running", reasoning: true }),
      item("r", 3, "process", { step: "reasoning", source: "model", title: "Überlegung des Modells", text: "Erst Termine prüfen.", status: "running" }),
      item("t", 4, "tool", { name: "calendar_list", status: "running", started_ts: Date.now() }),
      item("pt", 5, "process", { step: "tool", source: "protocol", title: "Werkzeug: calendar_list", status: "running", ref: "t" }),
    ];
    render(<ChatTimeline thread={thread(running, true)} />);
    const toggle = screen.getByRole("button", { name: /Gedankenprozess/ });
    expect(toggle.textContent).toContain("Werkzeug: calendar_list"); // live step on the collapsed bar
    fireEvent.click(toggle);
    expect(screen.getByText("Erst Termine prüfen.")).toBeTruthy();
    expect(screen.getByText("Modell")).toBeTruthy();
    expect(screen.queryByText(/keine Gedanken übermittelt/)).toBeNull();
    expect(screen.getByTitle("calendar_list: läuft")).toBeTruthy();
  });

  it("checklist clicks are stored on the message via the store", () => {
    render(<ChatTimeline thread={thread(events)} />);
    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[1] as HTMLElement);
    expect(toggleCheck).toHaveBeenCalledWith("s1", "m2", 1, true);
  });

  describe("generated images and videos", () => {
    const answer = (name: string, display: Record<string, unknown>, status = "ok") => [
      item("u", 1, "message", { role: "user", text: "Mach mir ein Bild" }),
      item("turn", 2, "turn", { status: "done", reasoning: false }),
      item("tool", 3, "tool", { name, tool_use_id: "toolu_m", status, started_ts: 0, ended_ts: 9000, duration_ms: 9000, display }),
      item("m", 4, "message", { role: "assistant", text: "Hier ist der **Leuchtturm**, Sir." }),
    ];

    it("shows the image inline (not the path), loaded by file name, and enlarges it on click", async () => {
      const file = "20261022-102738-53c75e52.png";
      const { container } = render(
        <ChatTimeline thread={thread(answer("image_generate", { kind: "image", media: "images", file, prompt: "Leuchtturm bei Nacht", aspect_ratio: "16:9" }))} />,
      );
      expect(screen.getByRole("status", { name: "Bild wird geladen" })).toBeTruthy(); // placeholder while loading
      const img = (await screen.findByRole("img", { name: "Erzeugtes Bild: Leuchtturm bei Nacht" })) as HTMLImageElement;
      expect(media).toHaveBeenCalledWith("images", file);
      expect(img.getAttribute("src")).toMatch(/^blob:/);
      expect(img.className).toContain("max-h-[26rem]"); // capped size in the chat
      expect(container.textContent).not.toContain("generated-images");
      // Markdown and the tool row keep working next to the image
      expect(container.querySelector("strong")?.textContent).toBe("Leuchtturm");
      expect(screen.getByTitle("image_generate: erfolgreich")).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Bild vergrößern" }));
      const dialog = screen.getByRole("dialog", { name: "Bild vergrößert" });
      expect(within(dialog).getByRole("img").getAttribute("src")).toBe(img.getAttribute("src"));
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("shows a placeholder when the image cannot be loaded or decoded", async () => {
      media.mockRejectedValueOnce(new Error("404"));
      render(<ChatTimeline thread={thread(answer("image_generate", { kind: "image", media: "images", file: "missing-1.png" }))} />);
      expect(await screen.findByRole("img", { name: "Bild konnte nicht geladen werden" })).toBeTruthy();
      cleanup();

      render(<ChatTimeline thread={thread(answer("image_generate", { kind: "image", media: "images", file: "corrupt-1.png" }))} />);
      fireEvent.error(await screen.findByRole("img", { name: "Erzeugtes Bild" }));
      expect(screen.getByRole("img", { name: "Bild konnte nicht geladen werden" })).toBeTruthy();
    });

    it("plays a generated video inline", async () => {
      const { container } = render(
        <ChatTimeline thread={thread(answer("video_generate", { kind: "video", media: "videos", file: "clip-1.mp4", duration_s: 5 }))} compact />,
      );
      await vi.waitFor(() => expect(container.querySelector("video")).toBeTruthy());
      const video = container.querySelector("video") as HTMLVideoElement;
      expect(media).toHaveBeenCalledWith("videos", "clip-1.mp4");
      expect(video.controls).toBe(true);
      expect(video.getAttribute("src")).toMatch(/^blob:/);
      expect(video.className).toContain("max-h-48"); // smaller in the chat dock
    });

    it("never requests anything for malformed attachments or failed calls", () => {
      for (const display of [
        { kind: "image", media: "images", file: "../secret.png" },
        { kind: "image", media: "images", file: "/Users/paul/x.png" },
        { kind: "image", media: "videos", file: "a.png" },
        { kind: "video", media: "videos", file: "a.png" },
      ]) {
        render(<ChatTimeline thread={thread(answer("image_generate", display))} />);
        cleanup();
      }
      render(<ChatTimeline thread={thread(answer("image_generate", { kind: "image", media: "images", file: "ok.png" }, "failed"))} />);
      expect(media).not.toHaveBeenCalled();
      expect(screen.queryByRole("img")).toBeNull();
    });
  });

  it("parallel tool calls get their own rows and a shared time axis", () => {
    const parallel = [
      item("u", 1, "message", { role: "user", text: "Beides" }),
      item("a", 2, "tool", { name: "web_search", tool_use_id: "a", status: "failed", started_ts: 0, ended_ts: 1000, duration_ms: 1000, error: "Zeitüberschreitung" }),
      item("b", 3, "tool", { name: "calendar_list", tool_use_id: "b", status: "ok", started_ts: 10, ended_ts: 300, duration_ms: 290 }),
    ];
    const { container } = render(<ChatTimeline thread={thread(parallel)} />);
    expect(screen.getAllByText("parallel")).toHaveLength(2);
    expect(container.querySelectorAll(".tool-track__bar")).toHaveLength(2);
    expect(screen.getByText("Zeitüberschreitung")).toBeTruthy(); // failure visible without expanding
    const names = [...container.querySelectorAll(".tool-call .type-data.truncate")].map((n) => n.textContent);
    expect(names).toEqual(["web_search", "calendar_list"]);
  });
});
