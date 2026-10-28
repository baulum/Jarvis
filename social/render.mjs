#!/usr/bin/env node
// Headless render for the Jarvis Core (core/src/jarvis/social/render.py). Cross-platform: Node ≥ 20 and the
// browser Remotion downloads into node_modules/.remotion (`npm run browser` = `node render.mjs --ensure`).
//
//   node render.mjs --props storyboard.json --out video.mp4 [--crf 18] [--concurrency 4] [--timeout 120000]
//   node render.mjs --check            → {"ok": true, "browser": "..."} or an error (dependency check for the Core)
//   node render.mjs --ensure           → downloads the browser for this platform
//   [--chrome-mode headless-shell|chrome-for-testing] overrides the browser choice below
//
// Progress and the result are JSON lines on stdout; errors go to stderr and exit code 1. The video is rendered
// without audio – the Core mixes the sound design with FFmpeg afterwards.
import { appendFile, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "true"]);
    return acc;
  }, []),
);
const emit = (o) => process.stdout.write(`${JSON.stringify(o)}\n`);
// Remotion's default of 30 s per delayRender/page load is too tight on a busy machine (voice models, builds,
// a coding job): the root component then "does not load" although nothing is wrong. 2 minutes is still a clear stop.
const TIMEOUT_MS = Number(args.timeout ?? 120000);
// macOS: Chrome for Testing (a real .app bundle). Started by the Jarvis Core, the Chrome Headless Shell's GPU
// process aborts with "icudtl.dat not found in bundle" (Chrome takes itself for bundled and looks for its ICU data
// in a bundle the shell does not have) and takes the whole browser down ("Target closed"); from a terminal it works,
// which made it look random. Linux/Windows: the Headless Shell, Remotion's default.
const CHROME_MODE = args["chrome-mode"] ?? (process.platform === "darwin" ? "chrome-for-testing" : "headless-shell");
// Browser console messages, kept for render-error.log next to the output when something fails.
const browserLog = [];
const onBrowserLog = (l) => {
  browserLog.push(`[${l.type}] ${l.text}`);
  if (browserLog.length > 200) browserLog.shift();
};

async function main() {
  const { bundle } = await import("@remotion/bundler");
  const { renderMedia, selectComposition, ensureBrowser } = await import("@remotion/renderer");
  const browser = await ensureBrowser({ logLevel: args.ensure ? "info" : "error", chromeMode: CHROME_MODE });
  if (args.ensure) {
    emit({ type: "ensure", ok: true, chromeMode: CHROME_MODE, path: browser?.path });
    return;
  }
  const chromiumOptions = args.gl ? { gl: args.gl } : {};
  const common = { logLevel: "error", timeoutInMilliseconds: TIMEOUT_MS, onBrowserLog, chromiumOptions, chromeMode: CHROME_MODE };
  const serve = () => bundle({ entryPoint: path.join(here, "src/index.ts"), publicDir: path.join(here, "public") });
  if (args.check) {
    // a real start: bundle, launch Chrome, evaluate the composition – "installed" is not the same as "works"
    await selectComposition({ serveUrl: await serve(), id: "PjeSocial", ...common });
    emit({ type: "check", ok: true, browser: CHROME_MODE, node: process.version });
    return;
  }
  if (!args.props || !args.out) throw new Error("--props und --out sind Pflicht");
  const inputProps = JSON.parse(await readFile(args.props, "utf8"));
  emit({ type: "stage", stage: "bundle" });
  const serveUrl = await serve();
  const composition = await selectComposition({ serveUrl, id: "PjeSocial", inputProps, ...common });
  emit({ type: "stage", stage: "render", frames: composition.durationInFrames });
  let last = -1;
  await renderMedia({
    ...common,
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: path.resolve(args.out),
    inputProps,
    muted: true,
    crf: Number(args.crf ?? 18),
    pixelFormat: "yuv420p",
    colorSpace: "bt709", // TV range + BT.709 tags: what Instagram/TikTok expect (Chrome for Testing frames are full range)
    imageFormat: "jpeg",
    jpegQuality: 92,
    concurrency: args.concurrency ? Number(args.concurrency) : null,
    onProgress: ({ progress }) => {
      const pct = Math.floor(progress * 100);
      if (pct >= last + 5) {
        last = pct;
        emit({ type: "progress", progress: pct });
      }
    },
  });
  emit({ type: "done", out: path.resolve(args.out), frames: composition.durationInFrames });
}

main().catch(async (e) => {
  // the first line is the message the Core shows; the full stack and the browser console go to render-error.log
  process.stderr.write(`${String(e?.message ?? e).split("\n")[0]}\n${e?.stack ?? e}\n`);
  if (args.out || args.check) {
    const log = args.out
      ? path.join(path.dirname(path.resolve(args.out)), "render-error.log")
      : path.join(here, "node_modules", ".render-check-error.log");
    const text = [new Date().toISOString(), `node ${process.version} ${CHROME_MODE} cwd=${process.cwd()}`, String(e?.stack ?? e), "--- browser ---", ...browserLog, ""];
    await appendFile(log, text.join("\n")).catch(() => {});
  }
  process.exit(1);
});
