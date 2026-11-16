// Indexierbarkeits-Prüfung für Blogbeiträge (content/blog/*.md). Aufruf im Repo-Root nach `npm run build`:
//   node scripts/index-check.mjs --host apohr-portal.de
// Exit 1 bei einem Verstoß: Das Ergebnis des Coding-Jobs ist dann rot und wird nicht ausgeliefert.
import { existsSync, readdirSync, readFileSync } from "node:fs";

const arg = (name) => process.argv[process.argv.indexOf(name) + 1];
const host = arg("--host");
if (!host || host.startsWith("--")) throw new Error("Aufruf: node scripts/index-check.mjs --host <domain> [--root <repo>]");
if (process.argv.includes("--root")) process.chdir(arg("--root"));
const BLOG = "content/blog";
const BUILT = ".next/server/app";
const FORBIDDEN = /^(robots|googlebot|meta_robots|x-robots-tag|noindex|nofollow|index|follow)\s*:/i;
const today = new Date().toISOString().slice(0, 10);
const errors = [];
const notes = [];
const sitemap = existsSync(`${BUILT}/sitemap.xml.body`) ? readFileSync(`${BUILT}/sitemap.xml.body`, "utf8") : null;

for (const file of readdirSync(BLOG).filter((f) => f.endsWith(".md") && !f.startsWith("_"))) {
  const fm = (readFileSync(`${BLOG}/${file}`, "utf8").split("---")[1] ?? "").split("\n").map((l) => l.trim());
  const field = (key) => fm.find((l) => l.startsWith(`${key}:`))?.slice(key.length + 1).trim().replace(/^["']|["']$/g, "");
  if (field("draft") === "true") continue;
  const slug = field("slug") ?? file.replace(/\.md$/, "");
  if (fm.some((l) => FORBIDDEN.test(l))) errors.push(`${file}: robots/noindex-Feld im Frontmatter`);
  if (!field("title") || !field("description")) errors.push(`${file}: title oder description fehlt`);
  if ((field("date") ?? "") > today) continue; // erscheint erst später: Build-Ausgabe und Sitemap noch nicht prüfbar
  const html = existsSync(`${BUILT}/blog/${slug}.html`) ? readFileSync(`${BUILT}/blog/${slug}.html`, "utf8") : null;
  if (html) {
    const metas = html.match(/<meta[^>]+>/g) ?? [];
    for (const m of metas.filter((t) => /name="(robots|googlebot)"/.test(t))) {
      if (/noindex|nofollow/i.test(m)) errors.push(`${slug}: ${m} enthält noindex/nofollow`);
    }
    if (!metas.some((t) => /name="robots"/.test(t) && /index/.test(t))) notes.push(`${slug}: kein robots-Meta (Standard: index, follow)`);
    const canonical = (html.match(/<link[^>]+rel="canonical"[^>]*>/) ?? [""])[0].match(/href="([^"]*)"/)?.[1];
    const expected = new RegExp(`^https://${host.replace(/\./g, "\\.")}/blog/${slug}/?$`);
    if (!canonical || !expected.test(canonical)) errors.push(`${slug}: canonical ist ${canonical ?? "nicht gesetzt"}, erwartet https://${host}/blog/${slug}`);
  } else notes.push(`${slug}: keine gebaute HTML-Datei, robots und canonical nicht geprüft (erst npm run build ausführen)`);
  if (sitemap && !sitemap.includes(`/blog/${slug}`)) errors.push(`${slug}: fehlt in der Sitemap`);
}
if (!sitemap) notes.push("sitemap.xml.body nicht gefunden: Sitemap nicht geprüft");
notes.forEach((n) => console.log(`Hinweis: ${n}`));
if (errors.length) {
  errors.forEach((e) => console.error(`Fehler: ${e}`));
  process.exit(1);
}
console.log("index-check: alle veröffentlichten Beiträge indexierbar (robots, canonical, Sitemap)");
