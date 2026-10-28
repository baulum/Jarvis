#!/usr/bin/env node
// Headless render for the Jarvis Core (core/src/jarvis/social/render.py). Cross-platform: Node ≥ 20 and the
// Chrome Headless Shell Remotion downloads into node_modules/.remotion (`npm run browser`).
//
//   node render.mjs --props storyboard.json --out video.mp4 [--crf 18] [--concurrency 4]
//   node render.mjs --check            → {"ok": true, "browser": "..."} or an error (dependency check for the Core)
//
// Progress and the result are JSON lines on stdout; errors go to stderr and exit code 1. The video is rendered
// without audio – the Core mixes the sound design with FFmpeg afterwards.
import { readFile } from "node:fs/promises";
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

async function main() {
  const { bundle } = await import("@remotion/bundler");
  const { renderMedia, selectComposition, ensureBrowser } = await import("@remotion/renderer");
  const browser = await ensureBrowser({ logLevel: "error" });
  if (args.check) {
    emit({ type: "check", ok: true, browser: browser?.type ?? "ok", node: process.version });
    return;
  }
  if (!args.props || !args.out) throw new Error("--props und --out sind Pflicht");
  const inputProps = JSON.parse(await readFile(args.props, "utf8"));
  emit({ type: "stage", stage: "bundle" });
  const serveUrl = await bundle({ entryPoint: path.join(here, "src/index.ts"), publicDir: path.join(here, "public") });
  const composition = await selectComposition({ serveUrl, id: "PjeSocial", inputProps, logLevel: "error" });
  emit({ type: "stage", stage: "render", frames: composition.durationInFrames });
  let last = -1;
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: path.resolve(args.out),
    inputProps,
    muted: true,
    crf: Number(args.crf ?? 18),
    pixelFormat: "yuv420p",
    imageFormat: "jpeg",
    jpegQuality: 92,
    concurrency: args.concurrency ? Number(args.concurrency) : null,
    logLevel: "error",
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

main().catch((e) => {
  process.stderr.write(`${e?.stack ?? e}\n`);
  process.exit(1);
});
