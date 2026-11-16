"""Setzt die Prompts der vier Blogpost-Zeitpläne (Apo HR und PJE Systems) auf indexierbar + conversion-orientiert.

Die Zeitpläne liegen nur zur Laufzeit in der Postgres-Tabelle ``schedules`` (Schedule.action["prompt"]), nicht im
Repo. Dieses Skript ändert ausschließlich das Feld ``prompt`` über die Core-API (``PATCH /api/schedules/{id}``,
Owner-Token wie bei ``jarvisctl``). Name, Cron und ID bleiben unverändert; andere Zeitpläne werden nicht angefasst.

    python scripts/blog-schedules/update_blog_prompts.py --print     # nur die vier Prompts ausgeben
    python scripts/blog-schedules/update_blog_prompts.py --dry-run   # gegen die Core-API prüfen, nichts schreiben
    python scripts/blog-schedules/update_blog_prompts.py             # Prompts schreiben und zurücklesen

Alternativ im Chat mit dem Werkzeug ``schedule_update`` (id + prompt)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INDEX_CHECK = (Path(__file__).with_name("index-check.mjs")).read_text(encoding="utf-8")


@dataclass(frozen=True)
class Site:
    name: str
    domain: str
    repo: str
    path: str
    slash: str  # Schreibweise interner Links und der Canonical-URL: Apo ohne, PJE mit abschließendem Schrägstrich
    topic: str
    audience: str
    guide_facts: str
    offer_source: str
    cta_targets: str
    mention_rule: str
    meta_description: str
    price_rule: str


APO = Site(
    name="Apo HR",
    domain="apohr-portal.de",
    repo="baulum/Apo_HR_Tool",
    path="/Users/paul/Documents/Programmieren/hr_portal/Apo_HR_Tool",
    slash="",
    topic="Personalverwaltung und HR in Apotheken (Dienstplan, Schichtplanung, Zeiterfassung, Urlaub und Abwesenheiten, "
    "Notdienst, Arbeitszeit, Personalmanagement)",
    audience="Apothekenleitung, Filialleitung, Inhaberinnen und Inhaber sowie Personalverantwortliche in Apotheken",
    guide_facts=(
        "CONTENT-GUIDE.md (Zielgruppe, Sie-Ansprache, keine Floskeln, Abschnitt 4 „Keine erfundenen Fakten“, "
        "Abschnitt 5 Rechtsthemen mit Quellen und reviewed-Datum, Abschnitt 8 Ablauf mit content/topics.json) sowie "
        "den Referenzbeitrag content/blog/dienstplan-apotheke-erstellen.md"
    ),
    offer_source=(
        "der Startseite (app/page.tsx, components/marketing/home/*), den Funktionsseiten (/produkt, "
        "/dienstplanung-apotheke, /schichtplanung-apotheke, /zeiterfassung-apotheke, /urlaubsplanung-apotheke, "
        "/notdienstplanung-apotheke, /mitarbeiterverwaltung-apotheke, /preise) und CONTENT-GUIDE.md Abschnitt 4"
    ),
    cta_targets=(
        "Testzugang über /app/signup (erlaubt: „30 Tage kostenlos testen, Zahlungsart erforderlich“; nicht: „ohne "
        "Kreditkarte“), Funktionsüberblick /produkt, passende Funktionsseite, Preisseite /preise und die "
        "Kontaktseite /kontakt, über die laut Website eine persönliche Online-Demo vereinbart wird. „Demo“ also nur "
        "so anbieten (Link auf /kontakt), kein anderes Angebot erfinden"
    ),
    mention_rule=(
        "Laut CONTENT-GUIDE.md steht höchstens ein Absatz über APOHR im Beitrag, am Ende, sachlich, mit Link auf die "
        "passende Funktionsseite. Dieser Absatz enthält den Call-to-Action am Ende. Der Call-to-Action in der Mitte "
        "ist ein einzelner Satz (zum Beispiel im > **Tipp:**-Kasten) mit Link, kein zweiter APOHR-Absatz. Das "
        "Seitenlayout setzt zusätzlich einen Kostenlos-testen-Block; er ersetzt den Call-to-Action im Text nicht"
    ),
    meta_description="description höchstens 160 Zeichen",
    price_rule="Preise nie im Text nennen, sondern auf die Preisseite verlinken",
)

PJE = Site(
    name="PJE Systems",
    domain="pje-systems.de",
    repo="baulum/Website-Neu",
    path="/Users/paul/Documents/PJE Systems/Website-Neu",
    slash="/",
    topic="Websites, Software, Automatisierung und IT-Service für kleine und mittlere Unternehmen",
    audience=(
        "laut Website lokale Betriebe in der Hallertau und Umgebung, kleine und mittlere Unternehmen bundesweit und, "
        "bei Computerhilfe, Privatpersonen; keine IT-Fachleute (CONTENT-GUIDE.md Abschnitt 1)"
    ),
    guide_facts=(
        "CONTENT-GUIDE.md (Abschnitt 3 Aufbau, 3a Keywords, 4 Fakten, 4a Zitierfähig schreiben, 4b Leistungen: nur "
        "Belegtes, 6 Verlinkung, 9 Ablauf mit content/topics.json und content/keywords.json, 10 Checkliste)"
    ),
    offer_source=(
        "lib/content.ts, den Seiten /leistungen/, /websites/, /leistungen/softwareentwicklung/, "
        "/leistungen/computerhilfe/, /preise/, /kontakt/, /projekt-check/ und dem Impressum sowie CONTENT-GUIDE.md "
        "Abschnitt 4b (Feld leistung des Themas in content/topics.json beachten)"
    ),
    cta_targets=(
        "Kontaktseite /kontakt/ und Anfrageseite /projekt-check/ („Projekt anfragen“), passende Leistungsseite, "
        "/preise/. „Erstberatung“ oder „Erstgespräch“ nur nennen, wenn lib/content.ts (offers.freeConsultation) und "
        "die Website es so bestätigen; „Angebot“ nur als Anfrage an PJE Systems formulieren, ohne Preis- oder "
        "Terminversprechen"
    ),
    mention_rule=(
        "Laut CONTENT-GUIDE.md kommt PJE Systems im Text höchstens zweimal vor (Hinweis auf die eigene Leistung und "
        "Abschluss); der Beitrag muss auch ohne diese Stellen nützlich sein. Der Call-to-Action am Ende steht im "
        "Abschluss (nächster Schritt mit Link auf /kontakt/ und die Zielseite), der in der Mitte ist ein einzelner "
        "Satz mit Link nach dem Praxisteil"
    ),
    meta_description="description 70 bis 160 Zeichen",
    price_rule=(
        "Preise von PJE Systems nur so, wie sie in lib/content.ts bzw. auf /preise/ stehen; im Zweifel nicht nennen, "
        "sondern auf /preise/ verlinken"
    ),
)

OPENINGS = {
    "vormittag": "Erstelle einen neuen Blogbeitrag für {name} ({domain})",
    "nachmittag": "Erstelle einen zweiten neuen Blogbeitrag des Tages für {name} ({domain})",
}
DEDUP = {
    "vormittag": "Prüfe zuerst die bestehenden Beiträge im Repo",
    "nachmittag": "Prüfe zuerst die bestehenden Beiträge im Repo (auch den heutigen Vormittagsbeitrag)",
}


def test_command(site: Site) -> str:
    return f"npm run blog:check && npm run build && node scripts/index-check.mjs --host {site.domain}"


def build_prompt(site: Site, slot: str) -> str:
    s = site
    return f"""{OPENINGS[slot].format(name=s.name, domain=s.domain)} zur Verbesserung von SEO und GEO (Zitierbarkeit durch KI-Suche) und zur Gewinnung von Anfragen. Der Beitrag muss indexierbar sein und Leser zu einer Anfrage führen. {DEDUP[slot]}, um keine Themen doppelt zu behandeln, und wähle ein neues, suchrelevantes Thema rund um {s.topic}. Zielgruppe: {s.audience}.

ABLAUF
1. Themenwahl: Nimm das nächste offene Thema aus content/topics.json (Ablauf in CONTENT-GUIDE.md); setze es sofort auf „in Arbeit“. Ist keines passend, wähle selbst ein Thema mit klarem Suchintent und prüfe per Grep in content/blog nach Hauptkeyword und Slug, dass es nicht schon beantwortet ist.
2. Starte mit coding_start_job einen Auftrag auf dem Projekt {s.path} (Repo {s.repo}). Gib dem Job alle Vorgaben dieses Prompts (A, B, C und das Skript am Ende) vollständig und wörtlich weiter. Der Job liest zuerst {s.guide_facts} und hält sich daran. Bei Widerspruch gelten die Faktenregeln der Website, A und B bleiben verbindlich.
3. Setze test_command genau auf: {test_command(s)}
   Der Runner liefert einen Job nur aus, wenn dieser Test grün ist. Ein roter Test ist die Freigabeschranke: Der Beitrag geht dann nicht live.
4. Berichte Sir kurz das Ergebnis: Thema, Slug, Ergebnis der noindex-Prüfung und von index-check, Teststatus. Endet der Job rot oder bricht die noindex-Prüfung ab, melde den Grund und veröffentliche nichts von Hand an der Prüfung vorbei.

A. INDEXIERBARKEIT (Pflicht für jeden Beitrag; Sir verlangt, dass Blogbeiträge immer indexiert werden)
- Format: Markdown-Datei content/blog/<slug>.md im bestehenden Frontmatter-Format der Website (title, description, slug = Dateiname, date, updated, category, tags, author, tldr, faq und was der Leitfaden sonst verlangt; Referenzbeitrag ansehen). Kein neues Format, kein HTML, keine Seite außerhalb von content/blog.
- robots: Der Beitrag wird mit „index, follow“ ausgeliefert. Dazu kein noindex, kein nofollow, kein Frontmatter-Feld robots, googlebot, meta_robots, x-robots-tag, noindex, nofollow, index oder follow, kein draft: true, kein Datum in der Zukunft und keine Sonderlogik für diesen Beitrag. Die Beitragsseite bekommt index, follow aus dem Template; ändere dafür nichts am Template.
- Canonical: Genau ein Canonical, und zwar auf die eigene absolute URL https://{s.domain}/blog/<slug>{s.slash} (Schreibweise wie bei den bestehenden Beiträgen). Das Template setzt ihn; prüfe ihn nach dem Build im gebauten HTML (.next/server/app/blog/<slug>.html). Nie ein Canonical auf eine andere Seite.
- Sitemap: Der Beitrag muss nach dem Build in sitemap.xml stehen (wird aus content/blog erzeugt, nichts von Hand eintragen, aber prüfen).
- Meta: eigener SEO-Titel (title, höchstens 60 Zeichen, eindeutig, Hauptkeyword natürlich vorn) und eindeutige Meta-Description ({s.meta_description}, nirgends sonst auf der Website verwendet, nennt Nutzen und Hauptkeyword einmal).
- Interne Verlinkung: mindestens ein Link auf eine passende Leistungs- bzw. Funktionsseite, dazu ein bis drei Links auf verwandte, bereits veröffentlichte Beiträge. Interne Links relativ{" mit abschließendem Schrägstrich" if s.slash else " ohne Domain"}, nur auf Seiten, die es gibt (blog:check meldet kaputte Links).
- noindex-Prüfung vor dem Veröffentlichen (Pflicht, Teil des Auftrags): (1) Fehlt scripts/index-check.mjs im Repo, lege es mit dem Inhalt am Ende dieses Prompts unverändert an. (2) Suche per Grep nach noindex, nofollow und „index: false“ in der neuen Beitragsdatei, in der Beitragsroute (app/blog/[slug]) und in der Metadata-/SEO-Hilfe, aus der sie robots bezieht. Treffer in der Beitragsdatei: beheben. Treffer im Template oder in der Route, die Beiträge betreffen: nicht am Template ändern und den Beitrag nicht an der Prüfung vorbeischleusen; der Test bleibt dann rot, und der Bericht nennt die Fundstelle. Kategorie- und Themenseiten dürfen laut Website noindex haben, Beiträge nie. (3) Nach npm run build muss node scripts/index-check.mjs --host {s.domain} ohne Fehler laufen (prüft Frontmatter, robots im gebauten HTML, Canonical und Sitemap).

B. CONVERSION (Pflicht: Der Beitrag soll Leads und Anfragen erzeugen, ohne Werbesprech)
- Suchintent: Ein klares Thema mit einer Frage, die Leser tatsächlich suchen, und konkretem Nutzen für die Zielgruppe. Die Antwort steht im tldr und im ersten Satz der passenden Überschrift.
- Einleitung: Benennt das Problem der Zielgruppe sofort, in den ersten zwei Sätzen, aus ihrem Alltag. Keine Vorrede, keine Allgemeinplätze.
- Praxis: Konkrete Praxishinweise, die auch ohne Produkt funktionieren: nummerierte Schritte, Checkliste oder Tabelle, ein als Beispiel gekennzeichnetes Szenario.
- Auflösung: Ein eigener Abschnitt zeigt, wie {s.name} das Problem löst. Das Angebot und die Leistungen darfst du nur aus den Inhalten der Website übernehmen, nämlich aus {s.offer_source}. Was dort nicht steht, wird nicht behauptet.
- Call-to-Action: Ein klarer Call-to-Action am Ende des Textes und, wo es zum Text passt, ein zweiter kurzer in der Mitte (nach dem Praxisteil). Der Linktext nennt die Handlung, nie „hier klicken“. Mögliche Ziele, nur soweit die Website sie bietet: {s.cta_targets}. {s.mention_rule}.
- Fehlt der Website ein passender Handlungsaufruf für das Thema, verlinke im Beitrag auf die bestehende Kontaktseite {"/kontakt/" if s.slash else "/kontakt"}.
- Keine erfundenen Preise, Zahlen, Prozentwerte, Zeitersparnis-Versprechen, Kundennamen, Zitate, Bewertungen, Referenzen, Garantien, Zertifikate oder Auszeichnungen. Zahlen nur mit Quelle oder als gekennzeichnetes Beispiel. {s.price_rule}. Rechtsangaben nur mit Paragraf und Quelle, keine Rechtsberatung. Ton: Sie-Ansprache, klar und konkret, keine Marketing-Floskeln.

C. FREIGABE VOR DEM VERÖFFENTLICHEN
- Der Beitrag gilt erst als freigegeben, wenn npm run blog:check, npm run build und node scripts/index-check.mjs --host {s.domain} grün sind und die noindex-Prüfung aus A keinen Treffer ergibt. Sonst veröffentlichst du nichts: Beitrag nicht ausliefern, Fehler und Fundstelle im Bericht an Sir nennen.

INHALT VON scripts/index-check.mjs (nur anlegen, falls die Datei im Repo fehlt; sonst unverändert lassen):
{INDEX_CHECK}"""


@dataclass(frozen=True)
class Target:
    sid: str
    name: str
    cron: str
    site: Site
    slot: str


TARGETS = (
    Target("50ed06fd-2ffa-4103-879c-d4fe05156da6", "Apo HR Blogpost Vormittag", "0 8 * * *", APO, "vormittag"),
    Target("91191ba8-5d39-4f19-b5dc-91d614225677", "Apo HR Blogpost Nachmittag", "0 14 * * *", APO, "nachmittag"),
    Target("0b473f3b-714b-4236-8f85-eb2727b37b66", "PJE Systems Blogpost Vormittag", "0 9 * * *", PJE, "vormittag"),
    Target("c3492925-9c19-40a5-b1fd-bfa77045a41b", "PJE Systems Blogpost Nachmittag", "0 15 * * *", PJE, "nachmittag"),
)


def prompts() -> dict[str, str]:
    return {t.sid: build_prompt(t.site, t.slot) for t in TARGETS}


def apply(dry_run: bool) -> int:
    from jarvis.cli import _api  # Owner-Token und Port wie bei jarvisctl

    rows: dict[str, dict[str, Any]] = {r["id"]: r for r in _api("GET", "/api/schedules")}
    for t in TARGETS:  # erst alles prüfen, dann schreiben
        row = rows.get(t.sid)
        if row is None or row["name"] != t.name or row["cron"] != t.cron or row["action"].get("type") != "prompt":
            print(f"ABBRUCH: {t.sid} ({t.name}) passt nicht zur Erwartung; nichts geändert.", file=sys.stderr)
            return 1
    for t in TARGETS:
        new = build_prompt(t.site, t.slot)
        if dry_run:
            print(f"[dry-run] {t.name}: {len(new)} Zeichen würden geschrieben")
            continue
        _api("PATCH", f"/api/schedules/{t.sid}", json={"prompt": new})
    if dry_run:
        return 0
    after: dict[str, dict[str, Any]] = {r["id"]: r for r in _api("GET", "/api/schedules")}
    for t in TARGETS:
        r = after[t.sid]
        same = (r["name"], r["cron"], r["enabled"]) == (t.name, t.cron, True)
        ok = same and r["action"].get("prompt") == build_prompt(t.site, t.slot).strip()
        print(f"{'OK ' if ok else 'FEHLER'} {t.name} ({t.sid}) cron={r['cron']} next={r['next_run_at']}")
        if not ok:
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print", action="store_true", help="die vier Prompts ausgeben und beenden")
    ap.add_argument("--dry-run", action="store_true", help="Erwartung gegen die Core-API prüfen, nichts schreiben")
    args = ap.parse_args()
    if args.print:
        for t in TARGETS:
            print(f"===== {t.name} | {t.sid} | {t.cron} =====\n{build_prompt(t.site, t.slot)}\n")
        return 0
    return apply(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
