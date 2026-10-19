"""Coding runner: delegates development jobs to the official Claude Code CLI.

Isolation & policy (see SECURITY.md):
* every job runs in its own git worktree under ``paths.workspaces_dir`` on a ``jarvis/*`` branch;
* ``claude -p`` runs with ``--restricted`` (ignores project/user settings and hooks, file tools
  confined to the worktree), ``--strict-mcp-config`` (no repo MCP servers), an explicit tool
  allowlist, ``--permission-mode acceptEdits`` + ``--permission-prompts none`` and the macOS
  Seatbelt bash sandbox with a strict network allowlist (``--settings``);
* API-key environment variables are stripped and ``claude auth status`` must report a
  claude.ai subscription login — Jarvis never bills the Anthropic API and never reads tokens;
* intended for Sir's own development work on his machine (Claude Code legal/usage terms).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from jarvis.db import session_scope
from jarvis.db.models import CodingJob
from jarvis.events import bus
from jarvis.security.audit import audit

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)

_STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_TEST_EXECUTABLES = {"npm", "pnpm", "npx", "node", "python3", "uv", "pytest", "cargo", "swift", "make"}
NETWORK_ALLOWLIST = [
    "registry.npmjs.org",
    "*.npmjs.org",
    "registry.yarnpkg.com",
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
]


def slug(text: str, n: int = 40) -> str:
    s = re.sub(
        r"[^a-z0-9]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    ).strip("-")
    return s[:n] or "job"


class CodingError(RuntimeError):
    pass


class CodingRunner:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.coding
        self.workspaces = state.settings.paths.workspaces_dir
        self.logs_dir = state.settings.paths.data_dir / "coding-logs"
        self._sem = asyncio.Semaphore(self.cfg.max_parallel_jobs)
        self._procs: dict[uuid.UUID, asyncio.subprocess.Process] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------ environment
    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV and not k.startswith("JARVIS_SECRET")}
        env["PATH"] = os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Package-manager caches live in a dedicated, sandbox-writable directory (not ~/.npm etc.).
        cache = self.workspaces / ".cache"
        env["npm_config_cache"] = str(cache / "npm")
        env["UV_CACHE_DIR"] = str(cache / "uv")
        env["PIP_CACHE_DIR"] = str(cache / "pip")
        env["PNPM_HOME"] = str(cache / "pnpm")
        return env

    async def auth_status(self) -> dict[str, Any]:
        exe = shutil.which(self.cfg.claude_binary)
        if exe is None:
            return {"ok": False, "error": "Claude Code CLI nicht gefunden"}
        proc = await asyncio.create_subprocess_exec(
            exe, "auth", "status", env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        try:
            data = json.loads(out.decode())
        except json.JSONDecodeError:
            return {"ok": False, "error": (err or out).decode()[:200]}
        method = data.get("authMethod")
        ok = bool(data.get("loggedIn")) and method in ("claude.ai", "oauth_token")
        return {
            "ok": ok,
            "authMethod": method,
            "loggedIn": data.get("loggedIn"),
            "error": None if ok else f"Nicht per Subscription angemeldet (authMethod={method})",
        }

    # ------------------------------------------------------------------ git helpers
    async def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd, env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if check and proc.returncode != 0:
            raise CodingError(f"git {' '.join(args[:2])}: {err.decode()[:300]}")
        return out.decode().strip()

    async def ensure_repo(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if not (path / ".git").exists():
            await self._git(path, "init", "-b", "main")
        if not await self._git(path, "rev-parse", "--verify", "HEAD", check=False):
            readme = path / "README.md"
            if not readme.exists():
                readme.write_text(f"# {path.name}\n", encoding="utf-8")
            await self._git(path, "add", "-A")
            await self._git(
                path, "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost", "commit", "-m", "Initial commit"
            )

    async def _commit_pending(self, repo: Path) -> None:
        """Jarvis-managed projects: commit prepared files (e.g. BRIEF.md) so the job's worktree sees them."""
        if not repo.resolve().is_relative_to(self.state.settings.paths.projects_dir.resolve()):
            return  # never touch Sir's other repositories
        if await self._git(repo, "status", "--porcelain", check=False):
            await self._git(repo, "add", "-A")
            await self._git(
                repo,
                "-c",
                "user.name=Jarvis",
                "-c",
                "user.email=jarvis@localhost",
                "commit",
                "-m",
                "Jarvis: Projektunterlagen vorbereitet",
            )

    async def clone(self, url: str, name: str | None = None) -> Path:
        if not re.match(r"^https://[\w.-]+/[\w.-]+/[\w.-]+(\.git)?/?$", url):
            raise CodingError("Nur https-Git-URLs der Form https://host/owner/repo sind erlaubt")
        target = self.workspaces / "repos" / slug(name or url.rstrip("/").split("/")[-1].removesuffix(".git"))
        if target.exists():
            raise CodingError(f"Zielordner existiert bereits: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Clone without checking out hooks/filters config; no code from the repo is executed.
        await self._git(target.parent, "clone", "--depth", "50", "--no-recurse-submodules", url, str(target))
        return target

    # ------------------------------------------------------------------ jobs
    async def create_job(
        self,
        title: str,
        prompt: str,
        repo_path: str,
        *,
        test_command: str | None = None,
        task_id: uuid.UUID | None = None,
        actor: str = "system",
    ) -> CodingJob:
        repo = Path(os.path.expanduser(repo_path)).resolve()
        home = Path.home().resolve()
        if not repo.is_relative_to(home):
            raise CodingError("Repository muss im Benutzerordner liegen")
        if test_command:
            argv = shlex.split(test_command)
            if not argv or argv[0] not in _TEST_EXECUTABLES:
                raise CodingError(f"Testbefehl nicht erlaubt (erlaubt: {', '.join(sorted(_TEST_EXECUTABLES))})")
        async with session_scope() as s:
            job = CodingJob(
                title=title[:300],
                prompt=prompt,
                repo_path=str(repo),
                status="queued",
                test_command=test_command,
                task_id=task_id,
            )
            s.add(job)
            await s.flush()
        await audit(actor, "coding.create", target=str(job.id), details={"title": title, "repo": str(repo)})
        await bus.publish("coding.updated", job_id=str(job.id), status="queued", title=title)
        self._tasks[job.id] = asyncio.create_task(self._run(job.id))
        return job

    async def resume_interrupted(self) -> None:
        """Jobs that were running when the core stopped are marked and can be retried."""
        async with session_scope() as s:
            rows = (await s.execute(select(CodingJob).where(CodingJob.status.in_(["running", "queued"])))).scalars()
            for job in rows:
                job.status = "failed"
                job.result = {
                    **(job.result or {}),
                    "error": "Unterbrochen durch Neustart – mit 'Wiederholen' "
                    "fortsetzen (Claude-Session wird fortgeführt).",
                }

    async def retry(self, job_id: uuid.UUID) -> None:
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status not in ("failed", "cancelled"):
                raise CodingError("Job kann nicht wiederholt werden")
            job.status = "queued"
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, resume=True))

    async def cancel(self, job_id: uuid.UUID, actor: str) -> bool:
        proc = self._procs.get(job_id)
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        task = self._tasks.get(job_id)
        if task:
            task.cancel()
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status in ("completed", "failed", "cancelled"):
                return False
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
        await audit(actor, "coding.cancel", target=str(job_id))
        await bus.publish("coding.updated", job_id=str(job_id), status="cancelled")
        return True

    async def delete(self, job_id: uuid.UUID, actor: str, delete_worktree: bool = False) -> dict[str, Any]:
        """Removes a failed/cancelled job from the job list. The branch is always kept; the
        worktree only when ``delete_worktree`` is set explicitly."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None:
                raise CodingError("Job nicht gefunden")
            if job.status not in ("failed", "cancelled"):
                raise CodingError(
                    f"Job hat Status '{job.status}' – entfernt werden können nur fehlgeschlagene "
                    "oder abgebrochene Jobs (failed/cancelled)."
                )
            task = self._tasks.get(job_id)
            if task and not task.done():
                raise CodingError("Job läuft noch und kann nicht entfernt werden")
            repo, worktree, branch = job.repo_path, job.worktree_path, job.branch
        worktree_removed = False
        if delete_worktree and worktree:
            wt = Path(worktree).resolve()
            if not wt.is_relative_to(self.workspaces.resolve()):
                raise CodingError("Worktree liegt außerhalb des Jarvis-Workspace und wird nicht entfernt")
            if wt.exists():
                await self._git(Path(repo), "worktree", "remove", "--force", str(wt))
                worktree_removed = True
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is not None:
                await s.delete(job)
        self._tasks.pop(job_id, None)
        await audit(
            actor,
            "coding.delete",
            target=str(job_id),
            details={"worktree": worktree, "worktree_removed": worktree_removed, "branch": branch},
        )
        await bus.publish("coding.deleted", job_id=str(job_id))
        return {
            "deleted": True,
            "worktree": worktree,
            "worktree_removed": worktree_removed,
            "branch": branch,
        }

    async def cancel_all(self) -> None:
        for jid in list(self._procs):
            await self.cancel(jid, "killswitch")

    async def log_tail(self, job_id: uuid.UUID, lines: int = 200) -> list[dict[str, Any]]:
        path = self.logs_dir / f"{job_id}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines()[-lines:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"type": "raw", "text": line[:500]})
        return out

    async def _update(self, job_id: uuid.UUID, **fields: Any) -> None:
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is not None:
                for k, v in fields.items():
                    setattr(job, k, v)

    async def _run(self, job_id: uuid.UUID, resume: bool = False) -> None:
        async with self._sem:
            async with session_scope() as s:
                job = await s.get(CodingJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                title, prompt, repo, test_cmd = job.title, job.prompt, Path(job.repo_path), job.test_command
                worktree, branch, claude_sid = job.worktree_path, job.branch, job.claude_session_id
            if self.state.kill_switch.engaged:
                await self._finish(job_id, title, "failed", {"error": "Not-Aus aktiv"})
                return
            try:
                auth = await self.auth_status()
                if not auth["ok"]:
                    raise CodingError(auth["error"])
                await self.ensure_repo(repo)
                if not worktree:
                    branch = f"jarvis/{slug(title, 30)}-{str(job_id)[:8]}"
                    wt = self.workspaces / f"{slug(repo.name, 30)}-{str(job_id)[:8]}"
                    wt.parent.mkdir(parents=True, exist_ok=True)
                    await self._commit_pending(repo)
                    await self._git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD")
                    worktree = str(wt)
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                log_path = self.logs_dir / f"{job_id}.jsonl"
                await self._update(
                    job_id, status="running", worktree_path=worktree, branch=branch, log_path=str(log_path)
                )
                await bus.publish("coding.updated", job_id=str(job_id), status="running", title=title, branch=branch)
                full_prompt = (
                    f"{prompt}\n\nArbeite ausschließlich in diesem Verzeichnis. Halte den Code sauber und "
                    "lauffähig. Wenn es Tests oder einen Build gibt, führe sie aus und behebe Fehler. "
                    "Fasse am Ende kurz zusammen, was du gemacht hast."
                )
                result = await self._claude(
                    job_id, Path(worktree), full_prompt, log_path, resume_session=claude_sid if resume else None
                )
                tests: dict[str, Any] | None = None
                if test_cmd and not result.get("is_error"):
                    tests = await self._run_tests(Path(worktree), test_cmd)
                    if not tests["passed"]:
                        fix_prompt = (
                            f"Der Testbefehl `{test_cmd}` schlägt fehl. Analysiere die Ausgabe und behebe "
                            f"die Ursache:\n\n{tests['output'][-6000:]}"
                        )
                        await bus.publish(
                            "coding.progress",
                            job_id=str(job_id),
                            text="Tests fehlgeschlagen – automatische Fehleranalyse",
                        )
                        result = await self._claude(
                            job_id, Path(worktree), fix_prompt, log_path, resume_session=result.get("session_id")
                        )
                        tests = await self._run_tests(Path(worktree), test_cmd)
                commit = await self._commit(Path(worktree), title)
                diffstat = (
                    await self._git(Path(worktree), "diff", "--stat", "HEAD~1", "HEAD", check=False) if commit else ""
                )
                status = "completed" if not result.get("is_error") and (tests is None or tests["passed"]) else "failed"
                await self._finish(
                    job_id,
                    title,
                    status,
                    {
                        "summary": str(result.get("result", ""))[:4000],
                        "num_turns": result.get("num_turns"),
                        "duration_ms": result.get("duration_ms"),
                        "tests": tests,
                        "commit": commit,
                        "diffstat": diffstat[-3000:],
                        "branch": branch,
                        "worktree": worktree,
                        "permission_denials": result.get("permission_denials", [])[:20],
                        "error": "; ".join(result.get("errors", []))[:1000] if result.get("is_error") else None,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Coding job %s failed", job_id)
                await self._finish(
                    job_id,
                    title,
                    "failed",
                    {"error": f"{type(e).__name__}: {e}"[:1000], "worktree": worktree, "branch": branch},
                )

    def _claude_argv(self, prompt: str, resume_session: str | None) -> list[str]:
        exe = shutil.which(self.cfg.claude_binary) or self.cfg.claude_binary
        settings: dict[str, Any] = {}
        if self.cfg.sandbox:
            settings["sandbox"] = {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "autoAllowBashIfSandboxed": False,
                "network": {"allowedDomains": NETWORK_ALLOWLIST, "strictAllowlist": True},
                "filesystem": {"allowWrite": [str(self.workspaces / ".cache")]},
            }
        argv = [
            exe,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--restricted",
            "--tools",
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "Bash",
            "Skill",
            "--plugin-dir",
            str(self.state.skills.plugin_dir),
            "--strict-mcp-config",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--allowedTools",
            *self.cfg.allowed_tools,
            "Skill",
            "--settings",
            json.dumps(settings),
        ]
        if resume_session:
            argv += ["--resume", resume_session]
        return argv

    async def _claude(
        self, job_id: uuid.UUID, cwd: Path, prompt: str, log_path: Path, resume_session: str | None = None
    ) -> dict[str, Any]:
        argv = self._claude_argv(prompt, resume_session)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=10 * 1024 * 1024,
        )
        self._procs[job_id] = proc
        await self._update(job_id, pid=proc.pid)
        result: dict[str, Any] = {}
        timeout = self.cfg.job_timeout_minutes * 60
        try:
            async with asyncio.timeout(timeout):
                assert proc.stdout is not None
                with log_path.open("a", encoding="utf-8") as log:
                    async for raw in proc.stdout:
                        line = raw.decode(errors="replace").strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            log.write(json.dumps({"type": "raw", "text": line[:2000]}) + "\n")
                            continue
                        compact = _compact_event(ev)
                        log.write(json.dumps(compact, ensure_ascii=False) + "\n")
                        log.flush()
                        if ev.get("type") == "system" and ev.get("subtype") == "init":
                            await self._update(job_id, claude_session_id=ev.get("session_id"))
                        elif ev.get("type") == "result":
                            result = ev
                        if compact.get("text") or compact.get("tool"):
                            await bus.publish("coding.progress", job_id=str(job_id), **compact)
                await proc.wait()
        except TimeoutError:
            os.killpg(proc.pid, signal.SIGTERM)
            raise CodingError(f"Zeitlimit von {self.cfg.job_timeout_minutes} Minuten überschritten") from None
        finally:
            self._procs.pop(job_id, None)
        if not result:
            err = (await proc.stderr.read()).decode()[-800:] if proc.stderr else ""
            raise CodingError(f"Claude Code beendet ohne Ergebnis (Exit {proc.returncode}): {err}")
        return result

    async def _run_tests(self, cwd: Path, command: str) -> dict[str, Any]:
        argv = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
        except TimeoutError:
            os.killpg(proc.pid, signal.SIGTERM)
            return {"passed": False, "exit_code": None, "output": "Timeout nach 15 Minuten"}
        return {
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": out.decode(errors="replace")[-8000:],
        }

    async def _commit(self, cwd: Path, title: str) -> str | None:
        if not await self._git(cwd, "status", "--porcelain"):
            return None
        await self._git(cwd, "add", "-A")
        await self._git(
            cwd,
            "-c",
            "user.name=Jarvis",
            "-c",
            "user.email=jarvis@localhost",
            "commit",
            "-m",
            f"Jarvis: {title}\n\nErstellt mit Claude Code über den Jarvis Coding-Runner.",
        )
        return await self._git(cwd, "rev-parse", "--short", "HEAD")

    async def _finish(self, job_id: uuid.UUID, title: str, status: str, result: dict[str, Any]) -> None:
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None:
                return
            if job.status == "cancelled":
                status = "cancelled"
            job.status = status
            job.result = json.loads(json.dumps(result, default=str))
            job.finished_at = datetime.now(UTC)
            task_id = job.task_id
        await audit("coding-runner", f"coding.{status}", target=str(job_id), details={"title": title})
        await bus.publish("coding.updated", job_id=str(job_id), status=status, title=title)
        await bus.publish(
            "coding.finished",
            job_id=str(job_id),
            status=status,
            title=title,
            summary=str(result.get("summary") or result.get("error") or "")[:800],
            task_id=str(task_id) if task_id else None,
        )

    async def prepare_pr(self, job_id: uuid.UUID, repo_full_name: str, base: str = "main") -> dict[str, Any]:
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status != "completed":
                raise CodingError("Nur abgeschlossene Jobs können als PR vorbereitet werden")
            wt, branch, title, res = Path(job.worktree_path), job.branch, job.title, dict(job.result or {})
        gh = self.state.connectors.connectors.get("github")
        if gh is None or not gh.active:
            raise CodingError("GitHub-Connector ist nicht eingerichtet")
        remotes = await self._git(wt, "remote")
        if "origin" not in remotes.split():
            raise CodingError("Repository hat kein 'origin'-Remote")
        await self._git(wt, "push", "-u", "origin", branch)
        body = f"{res.get('summary', '')}\n\n---\nTests: {res.get('tests')}\nErstellt von Jarvis mit Claude Code."
        pr = await gh.create_pr(repo_full_name, branch, base, f"Jarvis: {title}", body[:60000])  # type: ignore[attr-defined]
        return {"number": pr.get("number"), "url": pr.get("html_url")}


def _compact_event(ev: dict[str, Any]) -> dict[str, Any]:
    t = ev.get("type")
    out: dict[str, Any] = {"type": t, "ts": datetime.now(UTC).isoformat(timespec="seconds")}
    if t == "assistant":
        for block in ev.get("message", {}).get("content", []) or []:
            if block.get("type") == "text":
                out["text"] = str(block.get("text", ""))[:1500]
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                out["tool"] = block.get("name")
                out["detail"] = str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or "")[:300]
    elif t == "result":
        out.update({k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms")})
        out["text"] = str(ev.get("result", ""))[:1500]
    elif t == "system":
        out["subtype"] = ev.get("subtype")
    return out
