# ADR 0001 – Persistente Agent-Aufgaben, dauerhafte Verhaltensregeln, editierbarer System-Prompt

Status: umgesetzt (2026-11-11) · Betrifft: `core/src/jarvis/jobs/agent_task.py`, `jobs/engine.py`, `memory/rules.py`,
`agent/prompting.py`, `agent/persona.py`, Desktop-Einstellungen/Gedächtnis, Handy-App (Status/Fortschritt)

## Teil A – Aufgaben zuverlässig bis zum Abschluss

### Ursachen (Ist-Zustand vorher)
| Ursache | Fundstelle |
|---|---|
| Ein Chat-Auftrag war *ein* Claude-Turn; er endete beim ersten `end_turn` des Modells, das Ziel wurde nie geprüft | `agent/claude_agent.py` (`result` → `completed`), `agent/orchestrator.py::_run_claude` |
| Harte Grenze `agent.turn_timeout_s` = 900 s: danach Prozess getötet, Antwort = Fehlertext, keine Fortsetzung | `claude_agent.py::_attempt` |
| Core-Neustart (auch der automatische nach Jarvis-Jobs) beendet den Turn endgültig („Jarvis wurde beendet oder neu gestartet“); der Lauf war ein `spawn`-Task ohne Persistenz | `routes.py::chat`, `claude_agent.py::_ended` |
| Nur *ein* Wiederholungsversuch nach Absturz, ohne Backoff, ohne Fehlerklassifizierung | `claude_agent.py::chat_stream` |
| Kein Plan, keine Checkliste, keine Abschlussprüfung; „erfolgreicher Tool-Call = fertig“ | – |
| Das Task-System (`TaskEngine`) kannte nur deterministische Playbooks; für Agent-Läufe gab es keinen Task-Typ | `jobs/engine.py`, `jobs/playbooks.py` |
| Die Kopplung an die Client-Verbindung war *nicht* die Ursache: Die App ruft `POST /api/chat` ohne `wait` auf, der Turn läuft detached (bestätigt in `routes.py::chat`, `apps/jarvis_mobile/lib/state/chat.dart`). Verloren ging der Lauf durch Timeout/Neustart/Fehler, nicht durch Minimieren oder Display-Sperre | – |
| Kontextlimit: nur die Auto-Kompaktierung der CLI; keine strukturierte Übergabe | – |

### Entscheidung
Keine zweite Agent-Infrastruktur: Der Agent läuft als **Task der bestehenden `TaskEngine`** (Playbook `agent_task`,
ein Schritt „Plan, Ausführung und Abschlussprüfung“). Lease + Heartbeat + `FOR UPDATE SKIP LOCKED`, Events, Push
(`Notifier`), Pause/Abbrechen/Retry, Agent-Monitor und `task_*`-Tools funktionieren unverändert weiter.

**Schleife** (`AgentTaskService.run_step`): PLAN (Checkliste, persistiert) → EXECUTE (ein Agent-Turn pro Iteration über
`Orchestrator.handle(internal=True)`, eigene Chat-Session → eigene Timeline) → OBSERVE (Tool-Ereignisse → Ledger,
Fehler, aktuelle Aktivität) → VERIFY (strenger Prüfer, strukturierte Antwort, ohne Werkzeuge) → CONTINUE (neuer Turn mit
kompaktem Stand statt kompletter Historie). Der Run-Lock des Orchestrators wird nur *pro Turn* gehalten, Chat/Anrufe
laufen zwischen den Läufen weiter.

**Zustand** liegt in `Task.context["agent"]` (JSONB, keine Migration nötig): Ziel, Plan, Phase, Zähler, Ledger der
ausgeführten Tool-Calls, Notizen, letzte Antwort, Wartezustand. Checkpoint nach jedem Tool-Ergebnis und jedem Turn.

**Statusmodell** (`jobs/task_status.py`): gespeicherte Werte bleiben (keine Datenmigration, alte Clients laufen weiter);
neu: `retrying`, `blocked`. API/`task_status` liefern zusätzlich `phase` (kanonisch: queued, running,
waiting_for_user, retrying, blocked, failed, cancelled, completed; Extras `paused`, `waiting_external`).
`waiting_approval`/`waiting_input` → `waiting_for_user`. `blocked` ist über `retry` fortsetzbar.

**COMPLETED nur nach Abschlussprüfung** (`_apply`/`_completion_gate`): Prüfer sagt „Ziel erreicht“ *und* Beleg genannt
*und* alle Plan-Schritte done/skipped *und* keine ungelösten Tool-Fehler des letzten Laufs *und* keine wartende Freigabe.
Sonst: Fortsetzung mit „Prüfung – das fehlt noch“.

**Resume/Idempotenz**: Lease läuft ab oder der neue Core-Prozess gibt Leases früherer Prozesse desselben Hosts sofort frei
(`TaskEngine._reclaim_dead_workers`) → nächster Claim setzt am Checkpoint fort (`attempts` zählt). Ein unterbrochener Turn
(`in_flight`) wird beim Fortsetzen mit der Liste der bereits gelaufenen Aktionen aus dem Ledger eingeleitet („keine Aktion mit
Außenwirkung wiederholen, erst Stand prüfen“). Das Ledger überlebt den Neustart; außenwirksame Tool-Calls laufen weiter über
den `ToolExecutor` (Idempotenz-Ledger, Freigaben). Grenze: Ob das Modell die Anweisung befolgt, ist nicht erzwungen – ein
Tool-Call, der exakt im Moment des Absturzes lief, kann theoretisch doppelt auftreten; das Freigabesystem deckt die
kritischen Aktionen ab.

**Guards** (`agent.tasks.*` in `config.py`, großzügige Defaults): Wall-Clock (aktive Laufzeit, nicht Wartezeit) 6 h, 120 Läufe,
40 erzwungene Fortsetzungen, 4000 Tool-Calls; 6 Wiederholungen mit Backoff 8 s·2ⁿ (max 300 s) bei transienten Fehlern;
dieselbe fehlschlagende Aktion 3× → `blocked`; 3 Läufe ohne Fortschritt → `blocked`. Fehlerklassen: transient (Backoff),
Kontextlimit (neue Sitzung + kompakter Stand), Berechtigung/Anmeldung (`blocked`), permanent (zählt wie transient bis zum
Limit; das Modell korrigiert sich selbst), Freigabe nötig (`waiting_approval`), Nutzereingabe nötig (`waiting_input`).
„Kosten“ werden über die Zahl der Agent-Läufe/Tool-Calls begrenzt (Subscription: kein Preis pro Aufruf). Nach `retry` zählen
die Limits ab dem aktuellen Stand neu.

**Kontext**: Neue Agent-Sitzung nach `session_rotate_turns` (10) Läufen oder bei Kontextlimit; der Prompt enthält jedes Mal Ziel,
Plan-Checkliste, Notizen, fehlende Punkte, letztes Ergebnis – die Korrektheit hängt nicht am CLI-Gedächtnis.

**Wann läuft ein Chat-Auftrag als Task?** (1) Das Modell startet ihn mit `task_start_agent` (Regel A1 im Kern-Prompt);
(2) Aufträge vom Handy (`channel=mobile`), die klar mehrstufig sind (≥ 180 Zeichen und ≥ 2 Reihenfolge-Marker oder ≥ 3 Listenpunkte,
ohne Anhänge, nicht „direkt:“/„/…“) starten sofort als Task – ohne Modellturn (`Orchestrator._background_confirmation`);
(3) ein Chat-Turn, der nach ≥ 3 erfolgreichen Tool-Calls an Timeout/Absturz stirbt, wird als Task fortgesetzt
(`_continue_in_background`). Einfache Fragen bleiben unverändert. Anrufe (`phone`) nie automatisch; dort nur sinngemäß kurze Regel.

**Freigaben (A5)**: Das Freigabesystem ist unverändert. Eine im Lauf angeforderte Freigabe (`approval.requested` der Task-Session)
parkt den Task als `waiting_approval` (= WAITING_FOR_USER); `approval.decided` setzt ihn fort, die Entscheidung (freigegeben/
abgelehnt) steht im nächsten Prompt. Rückfragen stellt der Agent nur, wenn der Prüfer `needs_user` meldet → `waiting_input` +
Push (`task.input_required`); die Antwort (`answer`) wird beim Fortsetzen übergeben.

**App-Kette**: `POST /api/tasks/agent` (sofortige Task-ID), `GET /api/tasks[/{id}]` mit `phase` und `agent` (Ziel, Plan,
Aktivität, Zähler, Prüfung – identisch für Desktop, Handy und Agent-Monitor), `GET /api/tasks/{id}/log`, Abbrechen/Pause/Retry/Input
wie bisher, Live-Event `task.progress`, Push bei `task.completed|failed|blocked|input_required` und Freigabe.

## Teil B – Verhaltensregeln und System-Prompt

### Ursache vergessener Regeln
Regeln waren normale Erinnerungen (`kind: preference`) und erreichten das Modell nur über `MemoryService.context_for`:
semantische Trefferschwelle (score ≥ 0,42 oder Keyword), 1800 Zeichen *geteilt* mit Gesprächsnotizen, Anfragen < 8 Zeichen bekamen
nichts, bei Anrufen 250 ms Budget. Der Block steht im `<kontext>` der *Nutzernachricht* (kompaktierbarer Verlauf).
Hintergrund-Tasks, Scheduler-Läufe und Coding-Jobs erhielten ihn gar nicht bzw. nur zufällig. Widersprüchliche Präferenznotizen
(z. B. mehrere Modellwahl-Notizen) konkurrierten ohne „neuere gewinnt“-Regel.

### Entscheidung
* **Erinnerung vs. Regel**: neuer Notiz-Typ `kind: rule` im bestehenden Vault (Ordner `Regeln/`) mit Frontmatter `scope`
  (global|project), `project`, `active`, `topic`, `supersedes`, `migrated_from`. Kein neuer Speicher, keine DB-Spalte; Obsidian-bearbeitbar.
  `memory_save(kind="rule", scope, project)`, `memory_correct` (ersetzt, übernimmt Scope/Projekt, alte Regel bleibt `superseded`),
  `memory_rules_list`, `memory_rule_set_active`. Regeln erscheinen nicht mehr im „Möglicherweise relevantes Wissen“-Block.
* **Deterministisches Laden** (`RuleBook`): liest `Regeln/*.md` direkt aus dem Vault (mtime-Cache, kein Index/Embedding/DB).
  Aktiv = `active` ∧ nicht `superseded`; bei gleichem `topic` zählt nur die neuere.
* **Prompt-Reihenfolge** (`agent/prompting.py`): Kernanweisungen (Code: `persona.CORE_RULES` inkl. Sicherheit, Freigaben, Werkzeugregeln,
  Regel A1 + Tool-/Skill-/Host-/WhatsApp-Hinweise) → Sirs editierbarer Prompt → aktive globale Regeln → (pro Turn) Projektregeln →
  Kontext-Wissen. Globale Regeln + editierbarer Text stehen im Systemprompt (`--append-system-prompt`), nicht im Verlauf: sie überleben
  Kompaktierung, neue Sessions, `--resume`, Core-Neustart. Der Prozess trägt eine Signatur (Hash des Prompts); ändert sich Prompt oder Regelstand,
  startet die Unterhaltung vor dem nächsten Turn neu (Verlauf über `--resume`) – auch bei langen Tasks beim nächsten Lauf. Projektregeln
  (`match_projects`: ganze Wörter in Projektpfad/Task-Parametern/Auftragstitel; im Freitext ohne den Persona-Namen) kommen pro Turn in den
  Turn-Kontext; Coding-Jobs bekommen globale + Projektregeln im Auftrag (`CodingRunner._rules_block`).
* **Budget**: `agent.rules_budget_chars` (6000). Globale Regeln werden nie verworfen (gekürzt 600→400→240→140 Zeichen, sonst Warnung im Log
  und in der UI); Projektregeln füllen den Rest (neueste zuerst), Überlauf wird geloggt.
* **Migration**: `MemoryService.migrate_rules` (einmalig beim Start, Marker `data/rules-migrated`; erneut: `POST /api/memory/rules/migrate`,
  `dry_run`). Präferenznotizen mit Dauer-Formulierung („immer“, „nie“, „ab sofort“, „fest“ …) werden als Regeln *kopiert*; pro Thema (derzeit
  Modellwahl, Anrede) bleibt nur die neueste aktiv, ältere werden inaktiv mit Hinweis. Nichts wird gelöscht oder in den Altnotizen verändert.
* **Editierbarer System-Prompt**: `PromptStore` (`<data_dir>/system-prompt.json`, je Persona). Standard = Persona (Identität, Persönlichkeit,
  Stil, Humor) + nicht-sicherheitsrelevante Arbeitsstil-Bullets (`persona.DEFAULT_WORKSTYLE`). Wirkt ohne Neustart (Signatur, siehe oben).
  Kernanweisungen sind nur im Code und stehen im Prompt *über* dem editierbaren Teil mit ausdrücklichem Vorrang. API: `GET/PUT/DELETE
  /api/agent/system-prompt` (Schreiben nur Owner; Handy liest). UI: Einstellungen → „System-Prompt“, Gedächtnis → „Verhaltensregeln“
  (anzeigen, filtern, bearbeiten, (de)aktivieren, neu anlegen).

## Konfiguration
`config/jarvis.yaml` → `agent.rules_budget_chars`, `agent.tasks.{enabled, auto_background, auto_min_chars, resume_failed_turns,
max_wall_clock_s, max_turns, max_continuations, max_tool_calls, max_retries, retry_base_s, retry_max_s, repeat_limit, session_rotate_turns}`.

## Nicht gelöst / bewusst offen
* Handy-App: Status/Fortschritt in der Aktivitätsliste; kein eigener Editor für Regeln/System-Prompt (Desktop). APK muss neu gebaut werden.
* Wiederaufnahme garantiert keine exakt-einmal-Ausführung außenwirksamer Tool-Calls im Absturzmoment (siehe oben).
* Die Themenerkennung der Migration ist eine Heuristik (Modellwahl, Anrede); andere Widersprüche bleiben als getrennte aktive Regeln sichtbar und
  lassen sich in der UI deaktivieren.
