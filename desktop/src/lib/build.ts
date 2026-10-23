// The commit this app was built from (written in by vite.config.ts) and what the core says about it. The core
// always runs the newest code; this app only changes when scripts/install-app.sh builds it again. An app older
// than the UI code in the repository misses features the core already has (that is how Sir ended up without
// the paper clip and image previews in the chat).

declare const __BUILD_COMMIT__: string | undefined;

export const BUILD_COMMIT: string = typeof __BUILD_COMMIT__ === "string" ? __BUILD_COMMIT__ : "";

/** GET /api/system/desktop-build (core/src/jarvis/desktop_build.py) */
export interface DesktopBuild {
  build_commit: string;
  core_commit: string | null;
  /** null: could not be determined (no git, unknown commit) */
  stale: boolean | null;
  /** commits that changed the UI since the build */
  changes: number;
  reason?: string;
}

export function staleBuildNotice(b: DesktopBuild): { title: string; body: string } | null {
  if (b.stale !== true) return null;
  const what = b.changes === 1 ? "eine Änderung" : `${b.changes} Änderungen`;
  return {
    title: "Desktop-App ist veraltet",
    body: `Seit dem Build (${b.build_commit.slice(0, 7)}) gab es ${what} an der Oberfläche. Neu installieren: scripts/install-app.sh`,
  };
}
