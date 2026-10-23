import { execFileSync } from "node:child_process";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The commit the app is built from: the core tells whether the UI changed since (src/lib/build.ts).
function buildCommit(): string {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

// Tauri expects a fixed dev port; the core's CORS allowlist contains localhost:1420.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __BUILD_COMMIT__: JSON.stringify(buildCommit()) },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "localhost",
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: "safari16",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"], // *.test.tsx run in jsdom (see file header)
  },
});
