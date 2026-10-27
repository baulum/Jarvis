"""Coding runner: delegates development jobs to the official Claude Code CLI.

Isolation & policy (see SECURITY.md):
* every job runs in its own git worktree under ``paths.workspaces_dir`` on a ``jarvis/*`` branch;
* the CLI is started via ``coding.claude_command`` (default ``claudeb``, a shell alias that is expanded through
  ``coding.claude_aliases``); ``-p`` runs with ``--restricted`` (ignores project/user settings and hooks, file tools
  confined to the worktree), ``--strict-mcp-config`` (no repo MCP servers), an explicit tool
  allowlist, ``--permission-mode acceptEdits`` + ``--permission-prompts none`` and the macOS
  Seatbelt bash sandbox with a strict network allowlist (``--settings``);
* API-key environment variables are stripped and ``claude auth status`` must report a
  claude.ai subscription login — Jarvis never bills the Anthropic API and never reads tokens;
* intended for Sir's own development work on his machine (Claude Code legal/usage terms).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sqlalchemy import select

from jarvis.agent.model_router import EFFORTS, model_id, resolve_alias
from jarvis.coding import self_restart
from jarvis.coding.monitor import build_monitor, compact_events
from jarvis.config import CUSTOMER_SITES_DIR, REPO_ROOT
from jarvis.db import session_scope
from jarvis.db.models import CodingJob
from jarvis.events import bus, spawn
from jarvis.secrets import secret_store
from jarvis.security.audit import audit
from jarvis.tools.executor import ToolUserError

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)

# Where the Claude command is searched besides $PATH (the core often runs with a minimal launchd PATH).
_CLI_DIRS = ("~/.local/bin", "~/bin", "/opt/homebrew/bin", "/usr/local/bin")
# Pauses before the 1st/2nd/3rd automatic continuation after a transient API/network error.
RETRY_DELAYS = (30, 90, 180)
RETRY_PROMPT = (
    "Die Verbindung zur API war kurz unterbrochen. Mache genau dort weiter, wo du aufgehört hast, "
    "und schließe den ursprünglichen Auftrag ab."
)
_TRANSIENT_RE = re.compile(
    r"ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN|can'?t reach the api server|overloaded|"
    r"(?:API Error|status|HTTP)\D{0,12}\b5\d\d\b|internal server error|bad gateway|service unavailable|"
    r"gateway time-?out",
    re.IGNORECASE,
)
# Pauses between merge attempts while GitHub refuses with 405 because it is still computing the PR's mergeability or
# required status checks are pending (about 10 minutes in total).
MERGE_RETRY_DELAYS = (10, 20, 30, 60, 120, 180, 180)
_MERGE_PENDING_RE = re.compile(r"GitHub-Fehler 405\b")
SYSTEM_PROMPT_HINT = (
    "Führe Shell-Befehle einzeln aus: keine Verkettung mit &&, ; oder ||, möglichst keine Pipes und kein `cd x && …` "
    "(die Rechteprüfung bewertet jeden Teil eines zusammengesetzten Befehls einzeln; ein nicht freigegebener Teil "
    "blockiert den ganzen Befehl). Nutze zum Lesen bevorzugt Read, Glob und Grep."
)


def is_customer_site(path: Path) -> bool:
    """True for a folder below ``CUSTOMER_SITES_DIR`` (the folder itself does not count)."""
    resolved, root = path.resolve(), CUSTOMER_SITES_DIR.resolve()
    return resolved != root and resolved.is_relative_to(root)


def is_transient_error(text: str) -> bool:
    return bool(_TRANSIENT_RE.search(text or ""))


def _which(name: str, path: str | None) -> str | None:
    if not name.strip():
        return None
    search = [str(Path(d).expanduser()) for d in _CLI_DIRS]
    base = os.environ.get("PATH", "") if path is None else path
    return shutil.which(os.path.expanduser(name), path=os.pathsep.join([*([base] if base else []), *search]))


def resolve_command(
    command: str, aliases: dict[str, list[str]] | None = None, path: str | None = None
) -> list[str] | None:
    """argv prefix that starts ``command``, or None. A real program wins; otherwise a configured shell alias
    (``aliases[command]`` = [program, *extra args]) is expanded. Never falls back to another command."""
    exe = _which(command, path)
    if exe is not None:
        return [exe]
    expansion = (aliases or {}).get(command)
    if expansion:
        target = _which(expansion[0], path)
        if target is not None:
            return [target, *expansion[1:]]
    return None

_STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
# Jarvis' own service tokens that may sit in the core's environment: never handed to coding jobs.
_SECRET_ENV = ("VERCEL_TOKEN",)
_TEST_EXECUTABLES = {
    "npm",
    "pnpm",
    "npx",
    "node",
    "python3",
    "uv",
    "pytest",
    "cargo",
    "swift",
    "make",
    "flutter",
    "dart",
}


def split_test_command(command: str) -> list[list[str]]:
    """Steps of a test command (``a && b`` -> two argv lists). There is no shell: ``&&`` is the only operator,
    it runs the next step only if the previous one passed. Every step must start with an allowed executable."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="&|;")
    lexer.whitespace_split = True
    steps: list[list[str]] = [[]]
    for part in lexer:
        if part == "&&":
            steps.append([])
        elif part and set(part) <= set("&|;"):
            raise CodingError(f"Testbefehl: '{part}' geht nicht (keine Shell; Schritte nur mit && verketten)")
        else:
            steps[-1].append(part)
    for argv in steps:
        if not argv or argv[0] not in _TEST_EXECUTABLES:
            raise CodingError(f"Testbefehl nicht erlaubt (erlaubt: {', '.join(sorted(_TEST_EXECUTABLES))})")
    return steps


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


_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_SUPABASE_PATH_RE = re.compile(r"^(?:(?P<root>.+)/)?supabase/(?P<kind>functions|migrations)/(?P<rest>.+)$")


def parse_github_repo(url: str) -> str | None:
    """``owner/name`` of a github.com remote URL (https, ssh or scp style), else None."""
    m = _GITHUB_REMOTE_RE.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def supabase_projects(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Supabase parts found in a file listing: ``{project dir: {"functions": [...], "migrations": n}}``.
    The project dir is "" for ``supabase/`` at the repo root. Shared code (``_shared``) is not a function."""
    found: dict[str, dict[str, Any]] = {}
    for path in paths:
        m = _SUPABASE_PATH_RE.match(path)
        if m is None:
            continue
        info = found.setdefault(m["root"] or "", {"functions": set(), "migrations": 0})
        first, _, tail = m["rest"].partition("/")
        if m["kind"] == "migrations":
            if not tail and first.endswith(".sql"):
                info["migrations"] += 1
        elif tail and not first.startswith(("_", ".")):
            info["functions"].add(first)
    return {
        root: {"functions": sorted(info["functions"]), "migrations": info["migrations"]}
        for root, info in sorted(found.items())
        if info["functions"] or info["migrations"]
    }


# Database password for ``supabase db push``, kept only in Jarvis' Keychain (never in code, files or git). Sir's
# standard password goes under SUPABASE_PASSWORD_SECRET; a project's own password under
# ``supabase_password_secret(project_ref)``.
SUPABASE_PASSWORD_SECRET = "supabase-db-password"  # noqa: S105 - Keychain entry name, not a password
_SUPABASE_REF_RE = re.compile(r"^[a-z0-9]{6,40}$")
# The database refused the login: wrong password (Postgres SQLSTATE 28P01) or the CLI had none and wanted to prompt.
_SUPABASE_AUTH_RE = re.compile(
    r"password authentication failed|SQLSTATE 28P01|failed SASL auth|Enter your database password", re.IGNORECASE
)


def supabase_password_secret(project_ref: str) -> str:
    """Keychain name of the database password of one Supabase project (``supabase-db-password-<ref>``)."""
    if not _SUPABASE_REF_RE.match(project_ref):
        raise CodingError(f"Ungültige Supabase-Projekt-ID: {project_ref!r}")
    return f"{SUPABASE_PASSWORD_SECRET}-{project_ref}"


def supabase_project_ref(project_dir: Path) -> str | None:
    """Project ref of the linked Supabase project (``supabase link`` writes ``supabase/.temp/project-ref``)."""
    try:
        ref = (project_dir / "supabase" / ".temp" / "project-ref").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return ref if _SUPABASE_REF_RE.match(ref) else None


def _hide(text: str, secret: str | None) -> str:
    """``text`` without ``secret`` (also URL-encoded, as in a connection string)."""
    if not secret:
        return text
    for form in {secret, quote(secret, safe="")}:
        text = text.replace(form, "***")
    return text


# Commit identity when the global git config has none (overridable via JARVIS_GIT_NAME / JARVIS_GIT_EMAIL).
DEFAULT_GIT_NAME = "Paul"
DEFAULT_GIT_EMAIL = "paulhoflich@gmail.com"
_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def valid_git_email(email: str | None) -> bool:
    """A real address with a domain (``x@localhost``, ``t@t`` … are not: Vercel blocks deployments of such commits)."""
    return bool(email and _EMAIL_RE.match(email.strip()))


def _global_git_config(key: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "--global", "--get", key], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def git_identity() -> tuple[str, str]:
    """(name, email) for every commit of a coding job: Sir's global git identity; if it has no valid email,
    ``JARVIS_GIT_EMAIL`` (else ``DEFAULT_GIT_EMAIL``), the name likewise from ``JARVIS_GIT_NAME``."""
    name = _global_git_config("user.name") or os.environ.get("JARVIS_GIT_NAME", "").strip() or DEFAULT_GIT_NAME
    email = _global_git_config("user.email")
    if not valid_git_email(email):
        email = os.environ.get("JARVIS_GIT_EMAIL", "").strip()
        if not valid_git_email(email):
            email = DEFAULT_GIT_EMAIL
    return name, email


def slug(text: str, n: int = 40) -> str:
    s = re.sub(
        r"[^a-z0-9]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    ).strip("-")
    return s[:n] or "job"


class CodingError(RuntimeError):
    pass


class TransientCodingError(CodingError):
    """The CLI ended without a result because of a temporary API/network problem (retried automatically)."""


class CodingRunner:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.coding
        self.workspaces = state.settings.paths.workspaces_dir
        self.logs_dir = state.settings.paths.data_dir / "coding-logs"
        self._sem = asyncio.Semaphore(max(1, self.cfg.max_parallel_jobs))
        # Several jobs run at once, each in its own worktree. What touches a repository itself (creating/removing
        # worktrees, its checkout, merging into the base branch, deploying) runs one job at a time per repository;
        # customer sites are edited in place (no worktree), so their jobs wait for each other entirely.
        self._repo_locks: dict[Path, asyncio.Lock] = {}
        self._site_locks: dict[Path, asyncio.Lock] = {}
        self._procs: dict[uuid.UUID, asyncio.subprocess.Process] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._sessions: dict[uuid.UUID, str] = {}  # latest Claude session id per job (for automatic continuation)
        self.retry_delays: tuple[float, ...] = RETRY_DELAYS
        self.merge_retry_delays: tuple[float, ...] = MERGE_RETRY_DELAYS
        self._cancelled: set[uuid.UUID] = set()  # cancelled by Sir: the open work is committed and pushed
        self._paused: dict[uuid.UUID, float] = {}  # job id -> monotonic time the pause began
        self._timeouts: dict[uuid.UUID, asyncio.Timeout] = {}  # so a pause does not eat the job's time budget
        self._budget_left: dict[uuid.UUID, float] = {}  # seconds of the run's time budget left when it was paused
        self._inbox: dict[uuid.UUID, list[str]] = {}  # messages for the agent, delivered between two runs
        self._interrupted: set[uuid.UUID] = set()  # current run was stopped on purpose to deliver a message
        self._closing: set[uuid.UUID] = set()  # job is past its last agent run: no more messages
        self._identity: tuple[str, str] | None = None  # (name, email) of all commits, resolved on first use
        # Jobs that delivered changes to Jarvis itself: one restart for all of them once no job runs (self_restart)
        self._restart_jobs: list[uuid.UUID] = []
        self._restart_task: asyncio.Task[None] | None = None
        self.restart_poll_s: float = 5
        self.restart_status_poll_s: float = 2

    # ------------------------------------------------------------------ environment
    def git_identity(self) -> tuple[str, str]:
        identity = getattr(self, "_identity", None)
        if identity is None:
            identity = self._identity = git_identity()
        return identity

    def _env(self) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in _STRIP_ENV and k not in _SECRET_ENV and not k.startswith("JARVIS_SECRET")
        }
        extra = [os.path.expanduser(str(p)) for p in getattr(self.cfg, "extra_path", None) or []]
        env["PATH"] = ":".join([os.environ.get("PATH", ""), *extra, "/opt/homebrew/bin", "/usr/local/bin"])
        for k, v in (getattr(self.cfg, "claude_env", None) or {}).items():
            if k in _STRIP_ENV:
                raise CodingError(f"coding.claude_env darf {k} nicht setzen (nur Subscription-Login erlaubt)")
            env[k] = os.path.expanduser(str(v))
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Every commit (runner, agent, merges) carries Sir's identity: a made-up one like jarvis@localhost makes
        # Vercel block the deployment.
        name, email = self.git_identity()
        for role in ("AUTHOR", "COMMITTER"):
            env[f"GIT_{role}_NAME"] = name
            env[f"GIT_{role}_EMAIL"] = email
        # Package-manager caches live in a dedicated, sandbox-writable directory (not ~/.npm etc.).
        cache = self.workspaces / ".cache"
        env["npm_config_cache"] = str(cache / "npm")
        env["UV_CACHE_DIR"] = str(cache / "uv")
        env["PIP_CACHE_DIR"] = str(cache / "pip")
        env["PNPM_HOME"] = str(cache / "pnpm")
        return env

    def _claude_cmd(self) -> list[str]:
        command = self.cfg.claude_command
        cmd = resolve_command(command, getattr(self.cfg, "claude_aliases", None))
        if cmd is None:
            raise CodingError(
                f"{command} nicht gefunden (weder als Programm im PATH bzw. ~/.local/bin, ~/bin, /opt/homebrew/bin, "
                "/usr/local/bin noch als Alias in coding.claude_aliases aufzulösen). Es gibt bewusst keinen "
                "Fallback auf 'claude'."
            )
        return cmd

    async def auth_status(self) -> dict[str, Any]:
        try:
            cmd = self._claude_cmd()
        except CodingError as e:
            return {"ok": False, "error": str(e)}
        proc = await asyncio.create_subprocess_exec(
            *cmd, "auth", "status", env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return {"ok": False, "error": f"{self.cfg.claude_command} auth status antwortet nicht (30 s)"}
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

    async def _git_paths(self, cwd: Path, *args: str) -> set[str]:
        """Paths listed by a git command (run with ``-z``: no quoting of unusual file names)."""
        command, *rest = args
        return {p for p in (await self._git(cwd, command, "-z", *rest)).split("\0") if p}

    async def _local_changes(self, repo: Path) -> set[str]:
        """Files with open (staged or unstaged) changes plus untracked files – what a checkout must not lose."""
        changed = await self._git_paths(repo, "diff", "--name-only", "--no-renames", "HEAD")
        return changed | await self._git_paths(repo, "ls-files", "--others", "--exclude-standard")

    async def ensure_repo(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if not (path / ".git").exists():
            await self._git(path, "init", "-b", "main")
        if not await self._git(path, "rev-parse", "--verify", "HEAD", check=False):
            readme = path / "README.md"
            if not readme.exists():
                readme.write_text(f"# {path.name}\n", encoding="utf-8")
            await self._git(path, "add", "-A")
            await self._git(path, "commit", "-m", "Initial commit")

    async def _commit_pending(self, repo: Path) -> None:
        """Jarvis-managed projects: commit prepared files (e.g. BRIEF.md) so the job's worktree sees them."""
        if not repo.resolve().is_relative_to(self.state.settings.paths.projects_dir.resolve()):
            return  # never touch Sir's other repositories
        if await self._git(repo, "status", "--porcelain", check=False):
            await self._git(repo, "add", "-A")
            await self._git(repo, "commit", "-m", "Jarvis: Projektunterlagen vorbereitet")

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

    # ------------------------------------------------------------------ model / effort
    def resolve_model(self, requested: str | None) -> str:
        """Full model id: explicit alias/id, else ``coding.default_model``, else the model chosen in the chat."""
        if requested and requested.strip().lower() != "auto":
            alias = resolve_alias(requested)
            if alias is None:
                raise CodingError(f"Unbekanntes Modell '{requested}' (erlaubt: haiku, sonnet, opus, auto)")
            return model_id(alias)
        configured = getattr(self.cfg, "default_model", "") or ""
        alias = resolve_alias(configured)
        if configured and configured.lower() != "auto" and alias is None:
            raise CodingError(f"coding.default_model '{configured}' ist kein bekanntes Modell")
        if alias is None:
            router = getattr(self.state, "model_router", None)
            alias = resolve_alias(getattr(router, "default", None)) or "sonnet"
        return model_id(alias)

    def resolve_effort(self, requested: str | None) -> str | None:
        if requested is None or requested == "" or requested == "auto":
            router = getattr(self.state, "model_router", None)
            effort = getattr(router, "effort", None)
            return effort if effort in EFFORTS else None
        if requested not in EFFORTS:
            raise CodingError(f"Unbekannter Effort '{requested}' (erlaubt: {', '.join(EFFORTS)})")
        return requested

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
        model: str | None = None,
        effort: str | None = None,
    ) -> CodingJob:
        model_full = self.resolve_model(model)
        effort_value = self.resolve_effort(effort)
        repo = Path(os.path.expanduser(repo_path)).resolve()
        home = Path.home().resolve()
        if not repo.is_relative_to(home):
            raise CodingError("Repository muss im Benutzerordner liegen")
        if test_command:
            split_test_command(test_command)
        async with session_scope() as s:
            job = CodingJob(
                title=title[:300],
                prompt=prompt,
                repo_path=str(repo),
                status="queued",
                test_command=test_command,
                task_id=task_id,
                model=model_full,
                effort=effort_value,
            )
            s.add(job)
            await s.flush()
        await audit(
            actor,
            "coding.create",
            target=str(job.id),
            details={"title": title, "repo": str(repo), "model": model_full, "effort": effort_value},
        )
        await bus.publish("coding.updated", job_id=str(job.id), status="queued", title=title)
        self._start(job.id)
        return job

    def _start(self, job_id: uuid.UUID, resume: bool = False) -> None:
        task = asyncio.create_task(self._run(job_id, resume=resume))
        self._tasks[job_id] = task

        def forget(t: asyncio.Task[None]) -> None:
            if self._tasks.get(job_id) is t:
                self._tasks.pop(job_id, None)

        task.add_done_callback(forget)

    def _repo_lock(self, repo: Path) -> asyncio.Lock:
        return self._repo_locks.setdefault(repo.resolve(), asyncio.Lock())

    @contextlib.asynccontextmanager
    async def _slot(self, job_id: uuid.UUID) -> AsyncIterator[None]:
        """A place to run: one of ``coding.max_parallel_jobs``. Jobs of the same customer site (edited in place,
        no own worktree) additionally wait for each other – without holding a place meanwhile."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            repo = Path(job.repo_path) if job is not None else None
        async with contextlib.AsyncExitStack() as stack:
            if repo is not None and is_customer_site(repo):
                site = self._site_locks.setdefault(repo.resolve(), asyncio.Lock())
                if site.locked():
                    await bus.publish(
                        "coding.progress",
                        job_id=str(job_id),
                        type="waiting",
                        text=f"Wartet, bis der laufende Job für {repo.name} fertig ist",
                    )
                await stack.enter_async_context(site)
            if self._sem.locked():
                await bus.publish(
                    "coding.progress",
                    job_id=str(job_id),
                    type="waiting",
                    text=f"Wartet auf einen freien Platz (höchstens {self.cfg.max_parallel_jobs} Jobs gleichzeitig)",
                )
            await stack.enter_async_context(self._sem)
            yield

    async def resume_interrupted(self) -> list[tuple[uuid.UUID, str]]:
        """Jobs that were running (or paused) when the core stopped are marked and can be retried.
        Returns (id, title) of these jobs so Sir can be told – nothing else would ever mention them."""
        interrupted: list[tuple[uuid.UUID, str]] = []
        async with session_scope() as s:
            rows = (
                await s.execute(select(CodingJob).where(CodingJob.status.in_(["running", "queued", "paused"])))
            ).scalars()
            for job in rows:
                job.status = "failed"
                job.result = {
                    **(job.result or {}),
                    "error": "Unterbrochen durch Neustart – mit 'Wiederholen' "
                    "fortsetzen (Claude-Session wird fortgeführt).",
                }
                interrupted.append((job.id, job.title))
        return interrupted

    async def retry(self, job_id: uuid.UUID) -> None:
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status not in ("failed", "cancelled"):
                raise CodingError("Job kann nicht wiederholt werden")
            job.status = "queued"
            title = job.title
        await bus.publish("coding.updated", job_id=str(job_id), status="queued", title=title)
        self._start(job_id, resume=True)

    async def shutdown(self) -> None:
        """The core is stopping: end the agent processes. Left running they would keep editing a worktree nobody
        commits, and 'Wiederholen' after the restart would start a second agent on the same files. The jobs are
        marked as interrupted at the next start (``resume_interrupted``)."""
        for job_id, proc in list(self._procs.items()):
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    if job_id in self._paused:
                        os.killpg(proc.pid, signal.SIGCONT)
                    os.killpg(proc.pid, signal.SIGTERM)
        for task in list(self._tasks.values()):
            task.cancel()

    async def cancel(self, job_id: uuid.UUID, actor: str) -> bool:
        proc = self._procs.get(job_id)
        if proc and proc.returncode is None:
            try:
                if job_id in self._paused:  # a stopped process only sees SIGTERM after SIGCONT
                    os.killpg(proc.pid, signal.SIGCONT)
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._paused.pop(job_id, None)
        self._budget_left.pop(job_id, None)
        self._inbox.pop(job_id, None)
        self._interrupted.discard(job_id)
        self._closing.discard(job_id)
        task = self._tasks.get(job_id)
        if task and not task.done():
            self._cancelled.add(job_id)  # _run commits and pushes the work so far (see _after_cancel)
            task.cancel()
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status in ("completed", "failed", "cancelled"):
                return False
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
            title, task_id = job.title, job.task_id
        await audit(actor, "coding.cancel", target=str(job_id))
        await bus.publish("coding.updated", job_id=str(job_id), status="cancelled")
        # the cancelled run never reaches _finish: without this a task step waiting for the job (and a
        # "ruf mich an, sobald …" watch) would wait forever
        await bus.publish(
            "coding.finished",
            job_id=str(job_id),
            status="cancelled",
            title=title,
            summary="Abgebrochen",
            task_id=str(task_id) if task_id else None,
        )
        return True

    # ------------------------------------------------------------------ monitor / control
    def _log_path(self, job_id: uuid.UUID) -> Path:
        return self.logs_dir / f"{job_id}.jsonl"

    async def _log_entry(self, job_id: uuid.UUID, entry: dict[str, Any]) -> None:
        """Append a control entry (pause, message …) to the job log and push it to the live clients."""
        entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self._log_path(job_id).open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        await bus.publish("coding.progress", job_id=str(job_id), **entry)

    async def monitor(self, job_id: uuid.UUID) -> dict[str, Any]:
        """Live snapshot of one job (current step, tool calls with results, runtime, tokens, cost)."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None:
                raise CodingError("Job nicht gefunden")
            row = {
                "id": str(job.id),
                "title": job.title,
                "status": job.status,
                "model": job.model,
                "effort": job.effort,
                "branch": job.branch,
                "result": job.result,
                "created_at": job.created_at.isoformat(),
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
        entries = await self.log_tail(job_id, 5000)
        return build_monitor(row, entries, pending_messages=len(self._inbox.get(job_id, [])))

    async def pause(self, job_id: uuid.UUID, actor: str) -> bool:
        """Freezes the agent process (SIGSTOP on its process group); ``resume`` continues it exactly there."""
        proc = self._procs.get(job_id)
        if proc is None or proc.returncode is not None:
            raise CodingError("Job führt gerade keinen Agent-Schritt aus und kann nicht pausiert werden")
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status != "running":
                raise CodingError("Nur laufende Jobs können pausiert werden")
            job.status = "paused"
        try:
            os.killpg(proc.pid, signal.SIGSTOP)
        except ProcessLookupError as e:
            await self._update(job_id, status="running")
            raise CodingError("Agent-Prozess läuft nicht mehr") from e
        self._paused[job_id] = time.monotonic()
        cm = self._timeouts.get(job_id)
        when = cm.when() if cm is not None else None
        if cm is not None and when is not None:
            # the time budget stands still while paused: firing now would SIGTERM a stopped process
            # (delivered only after SIGCONT) and fail a job Sir merely paused
            self._budget_left[job_id] = max(0.0, when - asyncio.get_running_loop().time())
            cm.reschedule(None)
        await audit(actor, "coding.pause", target=str(job_id))
        await self._log_entry(job_id, {"type": "control", "action": "paused", "actor": actor})
        await bus.publish("coding.updated", job_id=str(job_id), status="paused")
        return True

    async def resume(self, job_id: uuid.UUID, actor: str) -> bool:
        proc = self._procs.get(job_id)
        began = self._paused.pop(job_id, None)
        if proc is None or proc.returncode is not None or began is None:
            raise CodingError("Job ist nicht pausiert")
        try:
            os.killpg(proc.pid, signal.SIGCONT)
        except ProcessLookupError as e:
            raise CodingError("Agent-Prozess läuft nicht mehr") from e
        self._restore_budget(job_id)
        await self._update(job_id, status="running")
        await audit(actor, "coding.resume", target=str(job_id))
        await self._log_entry(job_id, {"type": "control", "action": "resumed", "actor": actor})
        await bus.publish("coding.updated", job_id=str(job_id), status="running")
        return True

    def _restore_budget(self, job_id: uuid.UUID) -> None:
        """Re-arm the run's time budget with what was left when it was paused."""
        left = self._budget_left.pop(job_id, None)
        cm = self._timeouts.get(job_id)
        if cm is not None and left is not None:
            cm.reschedule(asyncio.get_running_loop().time() + left)

    async def send_message(self, job_id: uuid.UUID, text: str, actor: str, immediate: bool = False) -> dict[str, Any]:
        """Queues a message for the agent. The CLI takes its prompt on start, so the text reaches the agent as a
        follow-up turn of the same Claude session: after the current run ends – or, with ``immediate``, right now
        by stopping the current run (its work so far is kept; the session is resumed with the message)."""
        text = text.strip()
        if not text:
            raise CodingError("Nachricht ist leer")
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None:
                raise CodingError("Job nicht gefunden")
            status = job.status
        if status not in ("queued", "running", "paused") or job_id in self._closing:
            raise CodingError("Der Job nimmt keine Nachrichten mehr an (bereits im Abschluss oder beendet)")
        self._inbox.setdefault(job_id, []).append(text[:8000])
        await audit(actor, "coding.message", target=str(job_id), details={"immediate": immediate, "chars": len(text)})
        await self._log_entry(
            job_id, {"type": "user_message", "text": text[:2000], "immediate": immediate, "actor": actor}
        )
        interrupted = False
        proc = self._procs.get(job_id)
        if immediate and proc is not None and proc.returncode is None:
            self._interrupted.add(job_id)
            try:
                if job_id in self._paused:
                    os.killpg(proc.pid, signal.SIGCONT)
                    self._paused.pop(job_id, None)
                    self._restore_budget(job_id)
                    await self._update(job_id, status="running")
                os.killpg(proc.pid, signal.SIGTERM)
                interrupted = True
            except ProcessLookupError:
                self._interrupted.discard(job_id)
        await bus.publish("coding.updated", job_id=str(job_id), status="running" if interrupted else status)
        return {"queued": True, "interrupted": interrupted, "pending": len(self._inbox.get(job_id, []))}

    async def _drain_inbox(
        self,
        job_id: uuid.UUID,
        cwd: Path,
        log_path: Path,
        result: dict[str, Any],
        *,
        model: str | None,
        effort: str | None,
    ) -> dict[str, Any]:
        """Runs queued messages as follow-up turns of the job's Claude session."""
        while self._inbox.get(job_id):
            texts = self._inbox.pop(job_id)
            prompt = "Nachricht von Sir an dich, berücksichtige sie bei der weiteren Arbeit:\n\n" + "\n\n".join(texts)
            session = result.get("session_id") or self._sessions.get(job_id)
            result = await self._claude_retrying(
                job_id, cwd, prompt, log_path, resume_session=session, model=model, effort=effort
            )
            if result.get("is_error"):
                break
        return result

    async def delete(self, job_id: uuid.UUID, actor: str, delete_worktree: bool = False) -> dict[str, Any]:
        """Removes a failed/cancelled job from the job list. The branch is always kept; the
        worktree only when ``delete_worktree`` is set explicitly (open changes are committed to the branch first)."""
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
            repo, worktree, branch, title = job.repo_path, job.worktree_path, job.branch, job.title
        worktree_removed = False
        if delete_worktree and worktree:
            wt = Path(worktree).resolve()
            if not wt.is_relative_to(self.workspaces.resolve()):
                raise CodingError("Worktree liegt außerhalb des Jarvis-Workspace und wird nicht entfernt")
            if wt.exists():
                await self._commit(wt, title, wip=True)  # open changes stay on the (kept) branch
                async with self._repo_lock(Path(repo)):
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

    async def _new_worktree(self, job_id: uuid.UUID, repo: Path, title: str) -> tuple[str, str]:
        """Where a new job works: ``(worktree path, branch)``. Customer sites (below ``CUSTOMER_SITES_DIR``) are edited
        in place on their current branch; everything else gets its own worktree on a ``jarvis/*`` branch."""
        if is_customer_site(repo):
            return str(repo), await self._git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        branch = f"jarvis/{slug(title, 30)}-{str(job_id)[:8]}"
        wt = self.workspaces / f"{slug(repo.name, 30)}-{str(job_id)[:8]}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        await self._commit_pending(repo)
        await self._git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD")
        return str(wt), branch

    async def _run(self, job_id: uuid.UUID, resume: bool = False) -> None:
        async with self._slot(job_id):
            async with session_scope() as s:
                job = await s.get(CodingJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                title, prompt, repo, test_cmd = job.title, job.prompt, Path(job.repo_path), job.test_command
                worktree, branch, claude_sid = job.worktree_path, job.branch, job.claude_session_id
                job_model = job.model or self.resolve_model(None)  # jobs created before model selection
                job_effort = job.effort
            if self.state.kill_switch.engaged:
                await self._finish(job_id, title, "failed", {"error": "Not-Aus aktiv"})
                return
            try:
                auth = await self.auth_status()
                if not auth["ok"]:
                    raise CodingError(auth["error"])
                async with self._repo_lock(repo):
                    await self.ensure_repo(repo)
                    if not worktree:
                        worktree, branch = await self._new_worktree(job_id, repo, title)
                    elif not Path(worktree).is_dir():  # removed after a backup (see _ship_backup): bring it back
                        wt = Path(worktree)
                        await self._git(repo, "worktree", "prune", check=False)
                        ref = f"refs/heads/{branch}"
                        if await self._git(repo, "rev-parse", "--verify", "--quiet", ref, check=False):
                            await self._git(repo, "worktree", "add", str(wt), branch)
                        else:
                            await self._git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD")
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                log_path = self.logs_dir / f"{job_id}.jsonl"
                await self._update(
                    job_id, status="running", worktree_path=worktree, branch=branch, log_path=str(log_path)
                )
                await bus.publish("coding.updated", job_id=str(job_id), status="running", title=title, branch=branch)
                full_prompt = (
                    f"{prompt}\n\nArbeite ausschließlich in diesem Verzeichnis. Halte den Code sauber und "
                    "lauffähig. Wenn es Tests oder einen Build gibt, führe sie aus und behebe Fehler. "
                    "Fasse am Ende kurz zusammen, was du gemacht hast. Committen, Pushen, Mergen nach main und "
                    "Aufräumen übernimmt der Runner nach deinem Ende automatisch: Schreibe in der Zusammenfassung "
                    "nie, etwas sei nicht committet, gepusht oder gemergt (oder das müsse noch geschehen), und "
                    "erwähne keine abgelehnten Shell-Befehle oder nicht ausgeführte Tests/Builds als Mangel der "
                    "Auslieferung. Beschreibe nur die inhaltliche Änderung."
                )
                result = await self._claude_retrying(
                    job_id,
                    Path(worktree),
                    full_prompt,
                    log_path,
                    resume_session=claude_sid if resume else None,
                    model=job_model,
                    effort=job_effort,
                )
                wt_path = Path(worktree)
                result = await self._drain_inbox(job_id, wt_path, log_path, result, model=job_model, effort=job_effort)
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
                        result = await self._claude_retrying(
                            job_id,
                            Path(worktree),
                            fix_prompt,
                            log_path,
                            resume_session=result.get("session_id"),
                            model=job_model,
                            effort=job_effort,
                        )
                        tests = await self._run_tests(Path(worktree), test_cmd)
                while self._inbox.get(job_id) and not result.get("is_error"):  # sent during the tests
                    result = await self._drain_inbox(
                        job_id, wt_path, log_path, result, model=job_model, effort=job_effort
                    )
                    if test_cmd and not result.get("is_error"):
                        tests = await self._run_tests(wt_path, test_cmd)
                self._closing.add(job_id)  # no await since the last inbox check: later messages are refused
                self._inbox.pop(job_id, None)
                status = "completed" if not result.get("is_error") and (tests is None or tests["passed"]) else "failed"
                payload: dict[str, Any] = {
                    "summary": str(result.get("result", ""))[:4000],
                    "num_turns": result.get("num_turns"),
                    "duration_ms": result.get("duration_ms"),
                    "tests": tests,
                    "branch": branch,
                    "worktree": worktree,
                    "permission_denials": result.get("permission_denials", [])[:20],
                    "model": job_model,
                    "effort": job_effort,
                    "retries": result.get("_retries", 0),
                    "error": (
                        ("; ".join(result.get("errors") or []) or str(result.get("result", "")))[:1000]
                        if result.get("is_error")
                        else None
                    ),
                }
                await self._wrap_up(
                    job_id,
                    payload,
                    repo=repo,
                    worktree=wt_path,
                    branch=branch,
                    title=title,
                    green=status == "completed",
                    reason="Der Job ist fehlgeschlagen" if result.get("is_error") else "Die Tests sind rot",
                )
                await self._finish(job_id, title, status, payload)
                await self._schedule_self_restart(job_id, payload.get("ship"))
            except asyncio.CancelledError:
                if job_id in self._cancelled and worktree and branch:
                    await self._after_cancel(job_id, repo=repo, worktree=Path(worktree), branch=branch, title=title)
                raise
            except Exception as e:
                logger.exception("Coding job %s failed", job_id)
                failed: dict[str, Any] = {
                    "error": f"{type(e).__name__}: {e}"[:1000],
                    "worktree": worktree,
                    "branch": branch,
                }
                if worktree and branch and Path(worktree).is_dir():
                    await self._wrap_up(
                        job_id,
                        failed,
                        repo=repo,
                        worktree=Path(worktree),
                        branch=branch,
                        title=title,
                        green=False,
                        reason=f"Der Job ist abgebrochen ({type(e).__name__})",
                    )
                await self._finish(job_id, title, "failed", failed)
            finally:
                self._cancelled.discard(job_id)

    async def _wrap_up(
        self,
        job_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        repo: Path,
        worktree: Path,
        branch: str,
        title: str,
        green: bool,
        reason: str,
    ) -> None:
        """After every job end: commit what is still open (a WIP commit unless green), record the branch's commits –
        including the ones the agent made itself – and run the ship flow (green: merge, deploy, clean up; otherwise:
        back up the job branch, never main). Never raises (except cancellation); problems end up in ``payload``."""
        problems: list[str] = []
        try:
            await self._commit(worktree, title, wip=not green)
        except Exception as e:
            logger.exception("Coding job %s: commit failed", job_id)
            problems.append(f"Commit fehlgeschlagen: {e}")
        try:
            payload.update(await self._branch_work(worktree))
        except Exception as e:
            problems.append(f"Commits des Job-Branch nicht lesbar: {e}")
        jarvis = self_restart.is_jarvis_repo(repo)
        files = await self._changed_files(worktree) if jarvis else None  # before the clean-up removes the worktree
        if self.cfg.auto_ship:
            ship = await self._ship_flow(
                job_id,
                repo=repo,
                worktree=worktree,
                branch=branch,
                title=title,
                body=self._pr_body(payload),
                green=green,
                reason=reason,
            )
            if jarvis:
                self_restart.plan(ship, files, enabled=getattr(self.cfg, "self_restart", True))
            payload["ship"] = ship
            if ship["state"] == "failed" or ship.get("backup_error") or (ship.get("cleanup") or {}).get("errors"):
                problems.append(ship["message"])
        payload["delivery_error"] = "; ".join(problems)[:1000] or None

    async def _after_cancel(self, job_id: uuid.UUID, *, repo: Path, worktree: Path, branch: str, title: str) -> None:
        """Sir cancelled the job: the work so far is committed and the job branch pushed (never main), the result
        is added to the job (``cancel`` has already set the status)."""
        if not worktree.is_dir():
            return
        payload: dict[str, Any] = {"worktree": str(worktree), "branch": branch}
        try:
            await self._wrap_up(
                job_id,
                payload,
                repo=repo,
                worktree=worktree,
                branch=branch,
                title=title,
                green=False,
                reason="Der Job wurde abgebrochen",
            )
            async with session_scope() as s:
                job = await s.get(CodingJob, job_id)
                if job is not None:
                    job.result = json.loads(json.dumps({**(job.result or {}), **payload}, default=str))
            await bus.publish("coding.updated", job_id=str(job_id), status="cancelled", title=title)
        except Exception:
            logger.exception("Coding job %s: securing the cancelled work failed", job_id)

    def _read_dirs(self) -> list[Path]:
        dirs = [Path(os.path.expanduser(d)).resolve() for d in getattr(self.cfg, "extra_read_dirs", None) or []]
        return [d for d in dirs if d.is_dir()]

    def _claude_argv(
        self,
        prompt: str,
        resume_session: str | None,
        *,
        model: str | None = None,
        effort: str | None = None,
        continue_session: bool = False,
    ) -> list[str]:
        cmd = self._claude_cmd()
        restricted = getattr(self.cfg, "restricted", True)
        if restricted and "--dangerously-skip-permissions" in cmd:
            raise CodingError(
                "--dangerously-skip-permissions wird von --restricted verweigert: entweder das Flag aus "
                "coding.claude_aliases entfernen oder bewusst coding.restricted: false setzen."
            )
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
        read_dirs = [str(p) for p in self._read_dirs()]
        write_dirs = [str(Path(os.path.expanduser(str(p)))) for p in self.cfg.extra_write_dirs]
        if read_dirs or write_dirs:
            # Read-only extras get --add-dir plus an explicit Edit/Write deny; write extras are fully granted.
            read_only = [d for d in read_dirs if d not in write_dirs]
            settings["permissions"] = {"deny": [f"{t}(/{d}/**)" for d in read_only for t in ("Edit", "Write")]}
            if self.cfg.sandbox and write_dirs:
                settings.setdefault("sandbox", {}).setdefault("filesystem", {}).setdefault("allowWrite", []).extend(
                    write_dirs
                )
        add_dirs: list[str] = []
        for d in dict.fromkeys([*read_dirs, *write_dirs]):
            add_dirs += ["--add-dir", d]
        argv = [
            *cmd,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            *(["--restricted"] if restricted else []),
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
            *add_dirs,
        ]
        hint = SYSTEM_PROMPT_HINT
        if read_dirs:
            # read-only: "//" marks an absolute path in permission rules
            deny = [f"{tool}(/{d}/**)" for d in read_dirs for tool in ("Edit", "Write")]
            argv += ["--disallowedTools", *deny]
            hint += " Zusätzlich lesbar (nur lesen, nichts ändern): " + ", ".join(read_dirs) + "."
        argv += ["--append-system-prompt", hint]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if resume_session:
            argv += ["--resume", resume_session]
        elif continue_session:
            argv += ["--continue"]
        return argv

    async def _claude_retrying(
        self,
        job_id: uuid.UUID,
        cwd: Path,
        prompt: str,
        log_path: Path,
        *,
        resume_session: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """Runs the CLI; on transient API/network errors continues the same session (--resume / --continue)
        after 30 s / 90 s / 180 s, at most three times."""
        retries = 0
        session = resume_session
        while True:
            result: dict[str, Any] = {}
            try:
                result = await self._claude(
                    job_id,
                    cwd,
                    prompt,
                    log_path,
                    resume_session=session,
                    model=model,
                    effort=effort,
                    continue_session=retries > 0 and not session,
                )
                failure = (
                    "; ".join([*(result.get("errors") or []), str(result.get("result", ""))])
                    if result.get("is_error")
                    else ""
                )
                if not is_transient_error(failure):
                    if retries:
                        result["_retries"] = retries
                    return result
            except TransientCodingError as e:
                failure = str(e)
            session = self._sessions.get(job_id) or result.get("session_id") or session
            if retries >= len(self.retry_delays):
                msg = f"API/Netzwerk weiterhin nicht erreichbar nach {retries} automatischen Wiederholungen: {failure}"
                if result:
                    return {**result, "errors": [msg[:900]], "_retries": retries}
                raise CodingError(msg[:1000])
            delay = self.retry_delays[retries]
            retries += 1
            note = f"Netzwerk-/API-Fehler, Fortsetzung {retries}/{len(self.retry_delays)} in {delay:g} s"
            logger.warning("Coding job %s: %s (%s)", job_id, note, failure[:200])
            with log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"type": "retry", "text": f"{note}: {failure[:300]}"}, ensure_ascii=False) + "\n")
            await bus.publish("coding.progress", job_id=str(job_id), type="retry", text=note)
            await asyncio.sleep(delay)
            if getattr(getattr(self.state, "kill_switch", None), "engaged", False):
                raise CodingError("Not-Aus aktiv")
            prompt = RETRY_PROMPT

    async def _claude(
        self,
        job_id: uuid.UUID,
        cwd: Path,
        prompt: str,
        log_path: Path,
        resume_session: str | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        continue_session: bool = False,
    ) -> dict[str, Any]:
        argv = self._claude_argv(
            prompt, resume_session, model=model, effort=effort, continue_session=continue_session
        )
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
            async with asyncio.timeout(timeout) as budget:
                self._timeouts[job_id] = budget
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
                        entries = compact_events(ev)
                        for compact in entries:
                            log.write(json.dumps(compact, ensure_ascii=False) + "\n")
                        log.flush()
                        if ev.get("type") == "system" and ev.get("subtype") == "init":
                            if ev.get("session_id"):
                                self._sessions[job_id] = ev["session_id"]
                            await self._update(job_id, claude_session_id=ev.get("session_id"))
                        elif ev.get("type") == "result":
                            result = ev
                        for compact in entries:
                            if compact.get("text") or compact.get("tool") or compact.get("tool_id"):
                                await bus.publish("coding.progress", job_id=str(job_id), **compact)
                await proc.wait()
        except TimeoutError:
            await self._stop_process(proc)
            raise CodingError(f"Zeitlimit von {self.cfg.job_timeout_minutes} Minuten überschritten") from None
        finally:
            self._procs.pop(job_id, None)
            self._timeouts.pop(job_id, None)
            self._budget_left.pop(job_id, None)
        if not result and job_id in self._interrupted:
            # stopped on purpose to hand over a message: the session continues with it (see _drain_inbox)
            self._interrupted.discard(job_id)
            return {"is_error": False, "result": "", "session_id": self._sessions.get(job_id), "_interrupted": True}
        if not result:
            err = (await proc.stderr.read()).decode()[-800:] if proc.stderr else ""
            exc = TransientCodingError if is_transient_error(err) else CodingError
            raise exc(f"Claude Code beendet ohne Ergebnis (Exit {proc.returncode}): {err}")
        return result

    async def _run_tests(self, cwd: Path, command: str, timeout_s: float = 900) -> dict[str, Any]:
        """Runs the steps of ``command`` one after another (no shell); stops at the first failing step."""
        try:
            steps = split_test_command(command)
        except CodingError as e:
            return {"passed": False, "exit_code": None, "output": str(e)}
        deadline = asyncio.get_running_loop().time() + timeout_s
        output = ""
        code: int | None = 0
        env = self._env()
        src_dirs = [str(d) for d in (cwd / "core" / "src", cwd / "src") if d.is_dir()]
        if src_dirs:
            # Absolute paths into this worktree, ahead of any existing PYTHONPATH: `import jarvis` must not
            # pick up another checkout (a relative `-o pythonpath=` would resolve against the pytest rootdir).
            env["PYTHONPATH"] = os.pathsep.join([*src_dirs, *filter(None, [env.get("PYTHONPATH")])])
        for argv in steps:
            if len(steps) > 1:
                output += f"$ {shlex.join(argv)}\n"
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            left = max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=left)
            except TimeoutError:
                await self._stop_process(proc)
                output += f"Timeout nach {timeout_s / 60:g} Minuten"
                return {"passed": False, "exit_code": None, "output": output[-8000:]}
            output += out.decode(errors="replace")
            code = proc.returncode
            if code != 0:
                break
        output = output[-8000:]
        if src_dirs:
            output += await self._probe_jarvis_import(cwd, env)
        return {"passed": code == 0, "exit_code": code, "output": output}

    @staticmethod
    async def _probe_jarvis_import(cwd: Path, env: dict[str, str]) -> str:
        """Log line telling which ``jarvis`` package the test environment actually imports."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-c",
                "import jarvis; print(jarvis.__file__)",
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (OSError, TimeoutError):
            return "\n[jarvis-Import: Prüfung fehlgeschlagen]\n"
        lines = out.decode(errors="replace").strip().splitlines()
        return f"\n[jarvis geladen aus: {lines[-1] if lines else '?'}]\n"

    @staticmethod
    async def _stop_process(proc: asyncio.subprocess.Process, grace_s: float = 10) -> None:
        """SIGTERM to the process group; SIGKILL if it is still there after ``grace_s`` (never leave it behind)."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGCONT)  # a paused (SIGSTOPped) group only acts on SIGTERM once continued
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), grace_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)

    async def _commit(self, cwd: Path, title: str, wip: bool = False) -> str | None:
        """Commits open changes; returns the new commit or None if there was nothing to commit. ``wip`` marks the
        work of a red, failed or cancelled job (kept on its branch, never merged)."""
        if not await self._git(cwd, "status", "--porcelain"):
            return None
        await self._git(cwd, "add", "-A")
        message = (
            f"Jarvis (WIP): {title}\n\nZwischenstand eines nicht grünen oder abgebrochenen Jobs, automatisch "
            "gesichert vom Jarvis Coding-Runner."
            if wip
            else f"Jarvis: {title}\n\nErstellt mit Claude Code über den Jarvis Coding-Runner."
        )
        await self._git(cwd, "commit", "-m", message)
        return await self._git(cwd, "rev-parse", "--short", "HEAD")

    async def _branch_work(self, wt: Path) -> dict[str, Any]:
        """Commits on the job branch that are not on the base branch (local or origin) – no matter who made them:
        the agent often commits itself, then the runner's own commit is empty. ``commit`` is the branch head.
        A job that ran directly on the base branch (customer site edited in place) is compared with origin only –
        against itself it would never have commits; with an empty origin all of its commits are new."""
        base = getattr(self.cfg, "ship_base_branch", "main")
        on_base = await self._git(wt, "rev-parse", "--abbrev-ref", "HEAD", check=False) == base
        refs = [
            ref
            for ref in ((f"origin/{base}",) if on_base else (f"origin/{base}", base))
            if await self._git(wt, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        ]
        head = await self._git(wt, "rev-parse", "--short", "HEAD", check=False) or None
        if not refs and not on_base:  # no base branch to compare with
            return {"commit": head, "commits": None, "diffstat": ""}
        count = await self._git(wt, "rev-list", "--count", "HEAD", *(("--not", *refs) if refs else ()), check=False)
        commits = int(count) if count.isdigit() else 0
        fork = await self._git(wt, "merge-base", "HEAD", *refs, check=False) if commits and refs else ""
        diffstat = await self._git(wt, "diff", "--stat", fork, "HEAD", check=False) if fork else ""
        return {"commit": head if commits else None, "commits": commits, "diffstat": diffstat[-3000:]}

    async def _finish(self, job_id: uuid.UUID, title: str, status: str, result: dict[str, Any]) -> None:
        for bucket in (self._closing, self._interrupted):
            bucket.discard(job_id)
        self._inbox.pop(job_id, None)
        self._paused.pop(job_id, None)
        self._budget_left.pop(job_id, None)
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
        summary = str(result.get("summary") or result.get("error") or "")
        ship_note = str((result.get("ship") or {}).get("message") or "")
        if ship_note:
            # The runner's delivery result is authoritative and comes first: the agent's text is written before
            # commit / merge / push happen and may claim they did not.
            summary = f"{ship_note}\n\nZusammenfassung des Agenten:\n{summary[:500]}"
        await bus.publish(
            "coding.finished",
            job_id=str(job_id),
            status=status,
            title=title,
            summary=summary[:800],
            task_id=str(task_id) if task_id else None,
        )

    # ------------------------------------------------------------------ ship (standard flow after a green job)
    @staticmethod
    def _pr_body(res: dict[str, Any]) -> str:
        tests = res.get("tests")
        verdict = "nicht konfiguriert" if tests is None else ("bestanden" if tests.get("passed") else "fehlgeschlagen")
        return f"{res.get('summary', '')}\n\n---\nTests: {verdict}\nErstellt von Jarvis mit Claude Code."

    def _github(self) -> Any:
        gh = self.state.connectors.connectors.get("github")
        if gh is None or not gh.active:
            raise CodingError("GitHub-Connector ist nicht eingerichtet")
        return gh

    async def _exec(
        self, cwd: Path, argv: list[str], timeout_s: float, extra_env: dict[str, str] | None = None
    ) -> tuple[int | None, str]:
        """Runs a command (no shell); returns (exit code, combined output tail). Exit code None = timeout."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**self._env(), **(extra_env or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            await self._stop_process(proc)
            return None, f"Timeout nach {timeout_s / 60:g} Minuten"
        return proc.returncode, out.decode(errors="replace")[-600:]

    async def ship(self, job_id: uuid.UUID, actor: str) -> dict[str, Any]:
        """Runs the ship flow again for a finished green job whose delivery did not complete (e.g. missing
        permission, Supabase CLI not installed). Continues after a PR / merge that already happened."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status != "completed":
                raise CodingError("Nur abgeschlossene (grüne) Jobs können ausgeliefert werden")
            repo, wt, branch, title, res = (
                Path(job.repo_path),
                Path(job.worktree_path),
                job.branch,
                job.title,
                dict(job.result or {}),
            )
        prior = res.get("ship") or {}
        if prior.get("state") == "shipped":
            raise CodingError("Job ist bereits ausgeliefert")
        tests = res.get("tests")
        jarvis = self_restart.is_jarvis_repo(repo)
        files = await self._changed_files(wt) if jarvis and wt.is_dir() else None
        ship = await self._ship_flow(
            job_id,
            repo=repo,
            worktree=wt,
            branch=branch,
            title=title,
            body=self._pr_body(res),
            green=tests is None or bool(tests.get("passed")),
            reason="Die Tests sind rot",
            prior=prior,
            actor=actor,
        )
        if jarvis:
            self_restart.plan(ship, files, enabled=getattr(self.cfg, "self_restart", True))
        await self._update(job_id, result={**res, "ship": ship})
        await bus.publish("coding.updated", job_id=str(job_id), status="completed", title=title)
        await self._schedule_self_restart(job_id, ship)
        return ship

    async def _ship_flow(
        self,
        job_id: uuid.UUID,
        *,
        repo: Path,
        worktree: Path,
        branch: str,
        title: str,
        body: str,
        green: bool,
        reason: str,
        prior: dict[str, Any] | None = None,
        actor: str = "coding-runner",
    ) -> dict[str, Any]:
        """Green: push the job branch, open a (non-draft) PR, merge it, deploy Supabase, then delete the job branch
        (remote and local) and the worktree; after a merge the local project is fast-forwarded to origin when that is
        safe (``result["ship"]["local_sync"]``). A job that ran directly on the base branch (customer site) pushes
        that branch without a PR; an empty origin first gets the base branch (``result["ship"]["empty_repo"]``).
        Red / error / cancelled: the job branch is pushed as a backup (never the base branch), but nothing reaches
        main or Supabase and the worktree stays. Never raises (except cancellation); the outcome is the returned
        dict, stored as ``result["ship"]``."""
        base = self.cfg.ship_base_branch
        prior = prior or {}
        ship: dict[str, Any] = {
            "state": "not_shipped",
            "stage": None,
            "message": "",
            "branch": branch,
            "base": base,
            "pr": prior.get("pr"),
            "merged": bool(prior.get("merged")),
            "pushed": bool(prior.get("pushed")),
            "supabase": None,
        }
        # parallel jobs of one repository deliver one after another: the next one rebases onto the merged main
        async with self._repo_lock(repo):
            await self._ship_steps(
                ship, repo=repo, worktree=worktree, branch=branch, title=title, body=body, green=green, reason=reason
            )
        await audit(
            actor,
            "coding.ship",
            target=str(job_id),
            details={
                k: ship.get(k)
                for k in (
                    "state",
                    "stage",
                    "pr",
                    "merged",
                    "pushed",
                    "supabase",
                    "vercel",
                    "cleanup",
                    "local_sync",
                    "empty_repo",
                    "message",
                )
            },
        )
        return ship

    async def _ship_steps(
        self,
        ship: dict[str, Any],
        *,
        repo: Path,
        worktree: Path,
        branch: str,
        title: str,
        body: str,
        green: bool,
        reason: str,
    ) -> None:
        if not green:
            await self._ship_backup(ship, worktree, branch, reason)
        elif getattr(getattr(self.state, "kill_switch", None), "engaged", False):
            ship["message"] = f"Nicht ausgeliefert: Not-Aus aktiv. Job-Branch {branch} bleibt lokal bestehen."
        else:
            try:
                if not ship["merged"]:
                    await self._ship_merge(ship, worktree, branch, title, body)
                if ship["merged"]:
                    await self._ship_deploy(ship, repo)
            except Exception as e:  # any error (also network/HTTP) must end up in the result, not drop the job's
                pr = ship["pr"]
                ship["state"] = "failed"
                if ship["merged"] and pr:
                    aftermath = f"Der Merge ist bereits erfolgt (PR #{pr['number']}), nur der Deploy fehlt."
                elif branch == ship["base"]:
                    aftermath = (
                        f"{branch} ist bereits auf origin, nur der Deploy fehlt."
                        if ship["merged"]
                        else f"Nichts gepusht oder deployt; die Commits bleiben lokal auf {branch}."
                    )
                else:
                    aftermath = f"Nichts gemergt oder deployt; Job-Branch {branch} bleibt bestehen" + (
                        f", PR: {pr['url']}." if pr else "."
                    )
                ship["message"] = (
                    f"Auslieferung fehlgeschlagen ({ship['stage']}): {str(e) or type(e).__name__}. {aftermath}"[:900]
                )
            # a job that ran directly on the base branch has no job branch or worktree of its own to remove
            if (ship["merged"] or ship.get("nothing_to_ship")) and branch != ship["base"]:
                try:
                    await self._ship_cleanup(ship, repo, worktree, branch)
                except Exception as e:
                    ship.setdefault("cleanup", {}).setdefault("errors", []).append(str(e) or type(e).__name__)
                    ship["message"] = f"{ship['message']} Aufräumen fehlgeschlagen: {e}"[:900]
            if ship["merged"]:
                await self._ship_local_sync(ship, repo)

    async def _ship_local_sync(self, ship: dict[str, Any], repo: Path) -> None:
        """After the merge: bring Sir's local project (the main checkout, not the worktree) to the state on GitHub,
        fast-forward only. Another branch, a diverged history or open changes to files the merged commits touch are
        never overwritten, reset or discarded – the reason is recorded instead. Open changes to other files (Sir's
        work in progress) do not block it and stay as they are. Only a note: never marks the delivery as failed,
        never raises (except cancellation). The outcome is stored as ``ship["local_sync"]``."""
        base = ship["base"]
        sync: dict[str, Any] = {"state": "skipped", "reason": None, "head": None}
        ship["local_sync"] = sync
        try:
            if not repo.is_dir() or not await self._git(repo, "rev-parse", "--git-dir", check=False):
                sync["reason"] = f"Lokales Projekt {repo} nicht vorhanden"
            elif not await self._git(repo, "remote", "get-url", "origin", check=False):
                sync["reason"] = "Projekt hat kein 'origin'-Remote"
            else:
                await self._git(repo, "fetch", "origin")
                sync["head"] = await self._git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False) or None
                current = await self._git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
                if current != base:
                    where = "losgelöstem HEAD" if current == "HEAD" else f"Branch {current or '?'}"
                    sync["reason"] = f"Projekt steht auf {where}, nicht auf {base}"
                elif not await self._git(
                    repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}", check=False
                ):
                    sync["reason"] = f"origin/{base} nicht gefunden"
                elif (ahead := await self._git(repo, "rev-list", "--count", f"origin/{base}..HEAD")) != "0":
                    sync["reason"] = (
                        f"Lokaler {base} weicht von origin/{base} ab ({ahead} lokale Commits)"
                        " – kein Fast-Forward möglich"
                    )
                elif await self._git(repo, "rev-list", "--count", f"HEAD..origin/{base}") == "0":
                    sync["state"] = "synced"
                    sync["reason"] = "bereits aktuell"
                else:
                    local = await self._local_changes(repo)
                    incoming = await self._git_paths(
                        repo, "diff", "--name-only", "--no-renames", "HEAD", f"origin/{base}"
                    )
                    if clash := sorted(local & incoming):
                        more = f" (+{len(clash) - 5} weitere)" if len(clash) > 5 else ""
                        sync["reason"] = (
                            "Projekt hat nicht committete Änderungen an Dateien, die die Auslieferung ändert: "
                            + ", ".join(clash[:5])
                            + more
                        )
                    else:
                        await self._git(repo, "merge", "--ff-only", f"origin/{base}")
                        sync["state"] = "synced"
                        sync["head"] = await self._git(repo, "rev-parse", "HEAD")
                        if local:
                            sync["kept_changes"] = len(local)
        except Exception as e:
            sync["state"] = "failed"
            sync["reason"] = (str(e) or type(e).__name__)[:300]
        if sync["state"] == "synced":
            short = (sync["head"] or "")[:7]
            if sync["reason"] == "bereits aktuell":
                ship["message"] += f" Lokales Projekt war bereits auf dem Stand von origin/{base} ({short})."
            else:
                ship["message"] += f" Lokales Projekt auf origin/{base} aktualisiert ({short})."
            if sync.get("kept_changes"):
                ship["message"] += (
                    f" Nicht committete lokale Änderungen ({sync['kept_changes']} Dateien) bleiben erhalten."
                )
        elif sync["state"] == "failed":
            ship["message"] += f" Lokales Projekt nicht aktualisiert (Fehler): {sync['reason']}."
        else:
            ship["message"] += f" Lokales Projekt nicht aktualisiert: {sync['reason']}."

    async def _ship_backup(self, ship: dict[str, Any], wt: Path, branch: str, reason: str) -> None:
        """Not green: push the job branch (never main) so the work does not only exist in this worktree."""
        note = f"Nicht ausgeliefert: {reason}. Nichts in {ship['base']} gemergt oder deployt"
        if getattr(getattr(self.state, "kill_switch", None), "engaged", False):
            ship["message"] = f"{note}; Not-Aus aktiv – Job-Branch {branch} nur lokal gesichert (Worktree {wt})."
            return
        if branch == ship["base"]:  # the job ran directly on the base branch: a backup push would deliver it
            ship["message"] = f"{note}; der Job lief direkt auf {branch} – nichts gepusht, die Commits bleiben lokal."
            return
        ship["stage"] = "backup"
        try:
            if not (await self._branch_work(wt))["commits"]:
                ship["message"] = f"{note}; keine Änderungen auf dem Job-Branch {branch}, nichts zu sichern."
                await self._backup_remove_worktree(ship, wt, branch)
                return
            if await self._git(wt, "status", "--porcelain"):
                raise CodingError("Worktree enthält nicht committete Änderungen")
            if not await self._git(wt, "remote", "get-url", "origin", check=False):
                raise CodingError("Repository hat kein 'origin'-Remote")
            await self._git(wt, "push", "-u", "origin", branch)
        except Exception as e:
            ship["backup_error"] = (str(e) or type(e).__name__)[:500]
            ship["message"] = (
                f"{note}; Sichern des Job-Branch fehlgeschlagen: {ship['backup_error']}. "
                f"Branch {branch} nur lokal, Worktree {wt} bleibt."
            )[:900]
            return
        ship["pushed"] = True
        ship["message"] = f"{note}; Job-Branch {branch} auf origin gesichert."
        await self._backup_remove_worktree(ship, wt, branch)

    async def _backup_remove_worktree(self, ship: dict[str, Any], wt: Path, branch: str) -> None:
        """The work is safe on origin (or there is none): remove the worktree so it does not clutter the editor.
        'Wiederholen' recreates it from the job branch. A worktree with open changes stays."""
        try:
            if not wt.exists():
                return
            if not wt.resolve().is_relative_to(self.workspaces.resolve()):
                raise CodingError("liegt außerhalb des Jarvis-Workspace")
            if await self._git(wt, "status", "--porcelain"):
                raise CodingError("enthält nicht gesicherte Änderungen")
            repo = Path(await self._git(wt, "rev-parse", "--git-common-dir"))
            repo = (wt / repo).resolve().parent if not repo.is_absolute() else repo.resolve().parent
            await self._git(repo, "worktree", "remove", "--force", str(wt))
            ship["worktree_removed"] = True
            ship["message"] += " Worktree entfernt (Wiederholen legt ihn neu an)."
        except Exception as e:
            ship["message"] += f" Worktree {wt} bleibt: {e}"

    async def _ship_merge(self, ship: dict[str, Any], wt: Path, branch: str, title: str, body: str) -> None:
        base = ship["base"]
        ship["stage"] = "push"
        if await self._git(wt, "status", "--porcelain"):
            raise CodingError("Worktree enthält nicht committete Änderungen")
        origin = await self._git(wt, "remote", "get-url", "origin", check=False)
        if not origin:
            raise CodingError("Repository hat kein 'origin'-Remote")
        repo_name = parse_github_repo(origin)
        if repo_name is None:
            raise CodingError(f"origin ist kein GitHub-Repository: {origin}")
        empty = not await self._git(wt, "ls-remote", "--heads", "origin", f"refs/heads/{base}")
        if branch == base:  # customer site edited in place: no job branch, no PR
            await self._ship_on_base(ship, wt, empty)
            return
        gh = self._github()
        if empty:
            await self._ship_initial_base(ship, wt)
            if ship["merged"]:
                return
        await self._git(wt, "fetch", "origin", base)
        head = await self._git(wt, "rev-parse", "HEAD")
        ship["head"] = head
        if await self._git(wt, "rev-list", "--count", f"origin/{base}..HEAD") == "0":
            ship["nothing_to_ship"] = True
            ship["message"] = f"Nichts auszuliefern: keine Änderungen gegenüber {base}."
            return
        if not self.cfg.ship_via_pr and not ship["pr"]:
            ship["stage"] = "merge"
            try:
                await self._ship_direct(ship, wt, base)
                return
            except CodingError as e:  # conflict or protected main: fall back to the PR route below
                ship["direct_error"] = str(e)[:300]
                logger.info("Direkter Merge nach %s nicht möglich (%s), weiter per PR", base, e)
        await self._git(wt, "push", "-u", "origin", branch)
        ship["pushed"] = True
        if not ship["pr"]:
            ship["stage"] = "pr"
            pr = await gh.create_pr(repo_name, branch, base, f"Jarvis: {title}", body[:60000], draft=False)
            ship["pr"] = {"number": pr.get("number"), "url": pr.get("html_url")}
        ship["stage"] = "merge"
        merged = await self._merge_when_ready(gh, repo_name, ship["pr"]["number"], head)
        if not merged.get("merged", True):
            raise CodingError(str(merged.get("message") or "GitHub hat den Merge abgelehnt"))
        ship["merged"] = True
        ship["merge_sha"] = merged.get("sha")

    async def _ship_direct(self, ship: dict[str, Any], wt: Path, base: str) -> None:
        """No PR: rebase the job branch onto origin/<base> and push it straight to <base> (fast-forward only)."""
        for attempt in (1, 2):
            try:
                await self._git(wt, "rebase", f"origin/{base}")
            except CodingError as e:
                await self._git(wt, "rebase", "--abort", check=False)
                raise CodingError(f"Rebase auf {base} nicht möglich: {e}") from e
            head = await self._git(wt, "rev-parse", "HEAD")
            try:
                await self._git(wt, "push", "origin", f"HEAD:refs/heads/{base}")
            except CodingError:
                if attempt == 2:
                    raise
                await self._git(wt, "fetch", "origin", base)  # main moved meanwhile: rebase once more
                continue
            break
        ship["head"] = head
        ship["merged"] = True
        ship["merge_sha"] = head
        ship["direct"] = True

    async def _ship_on_base(self, ship: dict[str, Any], wt: Path, empty: bool) -> None:
        """The job ran directly on the base branch: push it to origin as it is (fast-forward only, a diverged
        origin is never overwritten). An empty origin gets the base branch as its first push."""
        base = ship["base"]
        head = await self._git(wt, "rev-parse", "HEAD")
        ship["head"] = head
        if not empty:
            await self._git(wt, "fetch", "origin", base)
            if await self._git(wt, "rev-list", "--count", f"origin/{base}..HEAD") == "0":
                ship["nothing_to_ship"] = True
                ship["message"] = f"Nichts auszuliefern: keine Änderungen gegenüber origin/{base}."
                return
        await self._git(wt, "push", "-u", "origin", base)
        ship["pushed"] = True
        ship["merged"] = True
        ship["merge_sha"] = head
        ship["direct"] = True
        ship["on_base"] = True
        if empty:
            ship["empty_repo"] = f"Leeres Repo: {base} initial gepusht."

    async def _ship_initial_base(self, ship: dict[str, Any], wt: Path) -> None:
        """origin is empty (freshly created on GitHub) and the job has its own branch: push the local base branch
        first so the PR has something to go into. Without a local base branch the job branch becomes the base."""
        base = ship["base"]
        if await self._git(wt, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}", check=False):
            await self._git(wt, "push", "-u", "origin", base)
            ship["empty_repo"] = f"Leeres Repo: {base} initial gepusht."
            return
        head = await self._git(wt, "rev-parse", "HEAD")
        await self._git(wt, "push", "origin", f"HEAD:refs/heads/{base}")
        ship["head"] = head
        ship["pushed"] = True
        ship["merged"] = True
        ship["merge_sha"] = head
        ship["direct"] = True
        ship["empty_repo"] = f"Leeres Repo: {base} aus dem Job-Branch angelegt und gepusht."

    async def _merge_when_ready(self, gh: Any, repo_name: str, number: int, head: str) -> Any:
        """Merges the PR. Right after it was opened GitHub may refuse with 405 (mergeability still being computed,
        required checks pending): retried for a while. Missing reviews or permissions are not waited for."""
        delays = list(self.merge_retry_delays)
        while True:
            try:
                return await gh.merge_pr(repo_name, number, sha=head, method=self.cfg.ship_merge_method)
            except ToolUserError as e:
                pending = _MERGE_PENDING_RE.search(str(e)) and "review" not in str(e).lower()
                if not pending or not delays or getattr(getattr(self.state, "kill_switch", None), "engaged", False):
                    raise
                delay = delays.pop(0)
                logger.info("PR #%s noch nicht mergebar (%s), neuer Versuch in %g s", number, e, delay)
                await asyncio.sleep(delay)

    async def _ship_cleanup(self, ship: dict[str, Any], repo: Path, wt: Path, branch: str) -> None:
        """Everything of the job is in the base branch: delete the job branch (origin and local) and the worktree.
        Only what is provably merged goes – a worktree with changes or a branch that moved on stays."""
        done: dict[str, Any] = {"remote_branch": False, "worktree": False, "local_branch": False, "errors": []}
        ship["cleanup"] = done
        errors: list[str] = done["errors"]
        if not branch.startswith("jarvis/"):
            errors.append(f"{branch} ist kein jarvis/*-Branch und bleibt")
        else:
            try:
                await self._git(repo, "push", "origin", "--delete", branch)
                done["remote_branch"] = True
            except CodingError as e:
                if "remote ref does not exist" in str(e):
                    done["remote_branch"] = True  # never pushed, or GitHub deleted it after the merge
                else:
                    errors.append(f"Remote-Branch: {e}")
            try:
                if wt.exists():
                    if not wt.resolve().is_relative_to(self.workspaces.resolve()):
                        raise CodingError("liegt außerhalb des Jarvis-Workspace")
                    if await self._git(wt, "status", "--porcelain"):
                        raise CodingError("enthält Änderungen, die nicht gemergt sind")
                    # --force only for ignored files (node_modules, .venv …); tracked and untracked work is clean
                    await self._git(repo, "worktree", "remove", "--force", str(wt))
                done["worktree"] = True
            except CodingError as e:
                errors.append(f"Worktree {wt}: {e}")
            if done["worktree"]:
                tip = await self._git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
                if tip and ship.get("head") and tip != ship["head"]:
                    errors.append(f"Lokaler Branch {branch} hat Commits nach dem Merge und bleibt")
                else:
                    try:
                        if tip:
                            await self._git(repo, "branch", "-D", branch)
                        done["local_branch"] = True
                    except CodingError as e:
                        errors.append(f"Lokaler Branch: {e}")
        if errors:
            ship["message"] = f"{ship['message']} Aufräumen unvollständig: {'; '.join(errors)}"[:900]
        else:
            ship["message"] = f"{ship['message']} Job-Branch (lokal und auf origin) und Worktree entfernt."

    async def _ship_deploy(self, ship: dict[str, Any], repo: Path) -> None:
        """After the merge: deploy Supabase migrations / edge functions found on the merged base branch. A refused
        database login leaves the delivery waiting for Sir's project password (``ship["state"] == "waiting_input"``)."""
        base, pr = ship["base"], ship["pr"]
        ship["stage"] = "deploy"
        await self._git(repo, "fetch", "origin", base)
        listing = await self._git(repo, "ls-tree", "-r", "--name-only", f"origin/{base}")
        projects = supabase_projects(listing.splitlines())
        if pr:
            merged_note = f"PR #{pr['number']} in {base} gemergt ({pr['url']})."
        elif ship.get("on_base"):
            pushed = "" if ship.get("empty_repo") else ", nach origin gepusht"
            merged_note = f"Job lief direkt auf {base}{pushed} – kein PR nötig."
        else:
            merged_note = f"Direkt nach {base} gemergt und gepusht."
        if ship.get("empty_repo"):
            merged_note = f"{ship['empty_repo']} {merged_note}"
        if not projects:
            ship["supabase"] = {"present": False, "note": "Supabase nicht im Projekt – übersprungen"}
            ship["state"] = "shipped"
            ship["message"] = f"Ausgeliefert: {merged_note} Supabase nicht im Projekt – Deploy übersprungen."
            await self._ship_vercel(ship, repo, listing.splitlines())
            return
        ship["supabase"] = {"present": True, "projects": projects, "deployed": []}
        cli = _which(self.cfg.supabase_command, None)
        if cli is None:
            raise CodingError(f"Supabase CLI '{self.cfg.supabase_command}' nicht gefunden")
        # The CLI works on the local checkout (project link lives there): bring it to the merged state first.
        if await self._git(repo, "rev-parse", "--abbrev-ref", "HEAD") != base:
            raise CodingError(f"Repository ist nicht auf {base} ausgecheckt – lokaler Stand nicht aktualisierbar")
        if await self._git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise CodingError("Repository hat nicht committete Änderungen – lokaler Stand nicht aktualisierbar")
        await self._git(repo, "merge", "--ff-only", f"origin/{base}")
        timeout_s = self.cfg.supabase_timeout_minutes * 60
        for root, info in projects.items():
            steps = []
            if info["migrations"]:
                steps.append(("db push", [cli, "db", "push", "--yes"]))
            if info["functions"]:
                steps.append(("functions deploy", [cli, "functions", "deploy"]))
            for name, argv in steps:
                ref, source, password = None, "none", None
                if name == "db push":  # only the migrations log in to the database; functions use the access token
                    # Order (see _supabase_password): standard password, then project password, then ask Sir.
                    ref = supabase_project_ref(repo / root)
                    source, password = self._supabase_password(ref)
                    ship["supabase"].setdefault("password", {})[root or "."] = source
                code, out = await self._exec(
                    repo / root, argv, timeout_s, {"SUPABASE_DB_PASSWORD": password} if password else None
                )
                out = _hide(out, password)
                if code != 0 and name == "db push" and _SUPABASE_AUTH_RE.search(out):
                    # Step 3: the password was refused – no further attempts, the delivery waits for Sir.
                    await self._supabase_ask_password(ship, root, ref, source, merged_note)
                    return
                if code != 0:
                    raise CodingError(f"supabase {name} in '{root or '.'}' fehlgeschlagen (Exit {code}): {out.strip()}")
                ship["supabase"]["deployed"].append(f"{root + ': ' if root else ''}{name}")
        ship["state"] = "shipped"
        ship["message"] = f"Ausgeliefert: {merged_note} Supabase deployt ({', '.join(ship['supabase']['deployed'])})."
        await self._ship_vercel(ship, repo, listing.splitlines())

    @staticmethod
    def _supabase_password(ref: str | None) -> tuple[str, str | None]:
        """(source, password) for ``supabase db push`` of one project. Exactly one password is tried per deploy:

        1. Standard password (Keychain ``supabase-db-password``): Sir mostly uses the same database password, so
           every project starts with it.
        2. Project password (Keychain ``supabase-db-password-<project-ref>``): once Sir has given one for this
           project (after step 3), it is preferred over the standard password for exactly this project.
        3. Asking Sir: if the database refuses the password used, nothing else is tried – the delivery waits
           (``ship["state"] == "waiting_input"``) and Sir is asked for this project's password
           (``_supabase_ask_password``, answered via ``coding_supabase_password``).

        Neither stored: no password is passed, the Supabase CLI uses its own login (``supabase link``)."""
        if ref and (project := secret_store.get(supabase_password_secret(ref))):
            return "project", project
        if standard := secret_store.get(SUPABASE_PASSWORD_SECRET):
            return "default", standard
        return "none", None

    async def _supabase_ask_password(
        self, ship: dict[str, Any], root: str, ref: str | None, tried: str, merged_note: str
    ) -> None:
        """Step 3 of ``_supabase_password``: the database refused the login. The delivery waits for Sir's project
        password (no further attempts, no password in the result or the message); Sir is notified."""
        where = f"'{root}'" if root else "Projektwurzel"
        refused = {
            "default": "mit dem Standard-Datenbankpasswort",
            "project": "mit dem hinterlegten Projektpasswort",
            "none": "ohne hinterlegtes Datenbankpasswort",
        }[tried]
        if ref is None:
            raise CodingError(
                f"Supabase-Anmeldung ({where}) {refused} abgelehnt, und das Projekt ist nicht per 'supabase link' "
                "verknüpft – ohne Projekt-ID lässt sich kein Projektpasswort zuordnen"
            )
        ship["state"] = "waiting_input"
        ship["supabase"]["password_request"] = {
            "project_ref": ref,
            "root": root,
            "tried": tried,
            "secret": supabase_password_secret(ref),
        }
        ship["message"] = (
            f"Auslieferung wartet: {merged_note} Supabase-Anmeldung für Projekt {ref} ({where}) {refused} "
            "abgelehnt – es wurde nicht weiter probiert. Ich brauche das Datenbankpasswort dieses Supabase-Projekts "
            "(coding_supabase_password); es wird im Schlüsselbund gespeichert und der Deploy fortgesetzt."
        )
        notifier = getattr(self.state, "notifier", None)
        if notifier is not None:
            try:
                await notifier.notify(
                    "coding.supabase_password",
                    f"Supabase-Passwort für Projekt {ref} benötigt",
                    f"Die Datenbank hat die Anmeldung {refused} abgelehnt. Der Deploy wartet, bis Sir das "
                    "Datenbankpasswort dieses Projekts nennt.",
                    level="warning",
                    data={"project_ref": ref, "root": root, "tried": tried},
                )
            except Exception:
                logger.warning("Benachrichtigung über fehlendes Supabase-Passwort fehlgeschlagen", exc_info=True)

    async def store_supabase_password(self, job_id: uuid.UUID, password: str) -> str:
        """Sir's answer to step 3: stores the database password of the Supabase project the job's delivery is
        waiting for in the Keychain (``supabase-db-password-<ref>``, used first from now on). Returns the ref."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            ship = dict((job.result or {}).get("ship") or {}) if job is not None else {}
        request = (ship.get("supabase") or {}).get("password_request") or {}
        if ship.get("state") != "waiting_input" or not request.get("project_ref"):
            raise CodingError("Die Auslieferung dieses Jobs wartet nicht auf ein Supabase-Passwort")
        if not password.strip():
            raise CodingError("Leeres Passwort")
        ref = str(request["project_ref"])
        secret_store.set(supabase_password_secret(ref), password)
        await audit("coding-runner", "coding.supabase_password", target=str(job_id), details={"project_ref": ref})
        return ref

    async def _ship_vercel(self, ship: dict[str, Any], repo: Path, listing: list[str]) -> None:
        """After merge (and Supabase): production deploy on Vercel if the project has a ``vercel.json`` or a local
        Vercel link (``.vercel/project.json``) and the Vercel connector is active with ``deploy_after_merge``.
        A Vercel failure does not undo the delivery: it is reported in the result and the message."""
        if "vercel.json" not in listing and not (repo / ".vercel" / "project.json").is_file():
            return
        vc: Any = self.state.connectors.connectors.get("vercel")  # VercelConnector (not imported: import cycle)
        if vc is None or not vc.active or not vc.settings.get("deploy_after_merge", True):
            ship["vercel"] = {"state": "skipped", "message": "Vercel-Connector nicht aktiv"}
            ship["message"] += " Vercel-Projekt erkannt, aber der Vercel-Connector ist nicht aktiv – kein Deploy."
            return
        origin = await self._git(repo, "remote", "get-url", "origin", check=False)
        repo_name = parse_github_repo(origin) if origin else None
        if repo_name is None:
            ship["vercel"] = {"state": "skipped", "message": "origin ist kein GitHub-Repository"}
            return
        try:
            result = await vc.deploy_after_merge(repo, repo_name, ship["base"])
        except Exception as e:
            ship["vercel"] = {"state": "failed", "error": (str(e) or type(e).__name__)[:300]}
            ship["message"] += f" Vercel-Deploy fehlgeschlagen: {ship['vercel']['error']} (erneut mit vercel_deploy)."
            return
        ship["vercel"] = {
            k: result.get(k) for k in ("state", "project", "deployment_id", "url", "production_url", "error", "message")
        }
        if result.get("message"):
            ship["message"] += f" {result['message']}"

    # ------------------------------------------------------------------ self restart (a job changed Jarvis itself)
    async def _changed_files(self, wt: Path) -> list[str] | None:
        """Files the job branch changes compared with the base branch; None if that cannot be determined."""
        base = getattr(self.cfg, "ship_base_branch", "main")
        try:
            refs = [
                ref
                for ref in (f"origin/{base}", base)
                if await self._git(wt, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
            ]
            if not refs:
                return None
            fork = await self._git(wt, "merge-base", "HEAD", *refs)
            return (await self._git(wt, "diff", "--name-only", fork, "HEAD")).splitlines()
        except (CodingError, OSError):
            return None

    def _restart_status_path(self) -> Path:
        return Path(self.state.settings.paths.data_dir) / self_restart.STATUS_FILE

    def _health_url(self) -> str:
        server = getattr(self.state.settings, "server", None)
        host = str(getattr(server, "host", "") or "127.0.0.1")
        if host in ("0.0.0.0", "::"):  # noqa: S104 - only mapped to the loopback address
            host = "127.0.0.1"
        return f"http://{host}:{getattr(server, 'port', 8765)}/api/health"

    async def _schedule_self_restart(self, job_id: uuid.UUID, ship: dict[str, Any] | None) -> None:
        """The job delivered changes to Jarvis itself (``self_restart.plan`` said so): restart Jarvis once the job's
        result is written and no coding job runs or waits any more. Never raises; problems go into the result."""
        if ((ship or {}).get("self_restart") or {}).get("state") != "pending":
            return
        try:
            if job_id not in self._restart_jobs:
                self._restart_jobs.append(job_id)
            # recorded at once: a restart by other means before this one also loads the new code (finish_self_restart)
            self_restart.write_status(
                self._restart_status_path(),
                {"phase": "pending", "jobs": [str(j) for j in self._restart_jobs], "requested_at": self_restart.now()},
            )
            if self._restart_task is None or self._restart_task.done():
                self._restart_task = spawn(self._restart_when_idle(), name="coding-self-restart")
        except Exception as e:
            logger.exception("Coding job %s: scheduling the Jarvis restart failed", job_id)
            msg = f"Neustart konnte nicht geplant werden: {str(e) or type(e).__name__}"
            with contextlib.suppress(Exception):
                await self._set_self_restart(
                    [job_id], {"state": "failed", "message": msg}, warning=f"Jarvis-Neustart: {msg}"
                )

    async def _restart_when_idle(self) -> None:
        """Waits until no coding job runs or waits any more – the jobs that asked for the restart must have ended
        too – then hands the restart to the helper process, which outlives this core (``self_restart.launch``)."""
        noted: set[uuid.UUID] = set()
        while True:
            await asyncio.sleep(self.restart_poll_s)
            busy = {jid for jid, task in self._tasks.items() if not task.done()}
            if not busy:
                break
            others = busy - set(self._restart_jobs)
            if others - noted:
                noted |= others
                ids = sorted(str(j) for j in others)
                await self._set_self_restart(
                    list(self._restart_jobs),
                    {
                        "state": "waiting",
                        "message": "Neustart verschoben, bis die laufenden Coding-Jobs fertig sind: "
                        + ", ".join(i[:8] for i in ids),
                        "waiting_for": ids,
                    },
                )
        jobs, self._restart_jobs = list(self._restart_jobs), []
        path = self._restart_status_path()
        if getattr(getattr(self.state, "kill_switch", None), "engaged", False):
            with contextlib.suppress(OSError):
                path.unlink()
            await self._set_self_restart(jobs, {"state": "skipped", "message": "Kein Neustart: Not-Aus aktiv."})
            return
        try:
            self_restart.write_status(
                path, {"phase": "started", "jobs": [str(j) for j in jobs], "requested_at": self_restart.now()}
            )
            await self._set_self_restart(
                jobs,
                {
                    "state": "started",
                    "message": "Neustart läuft: Core und, falls offen, Desktop-App werden neu gestartet.",
                    "started_at": self_restart.now(),
                },
            )
            await audit("coding-runner", "coding.self_restart", details={"jobs": [str(j) for j in jobs]})
            paths = self.state.settings.paths
            pid = self_restart.launch(
                path,
                log=Path(getattr(paths, "log_dir", paths.data_dir)) / self_restart.LOG_FILE,
                core_pid=os.getpid(),
                health_url=self._health_url(),
                root=REPO_ROOT,
                run_dir=Path(paths.data_dir) / "run",
            )
            logger.info("Jarvis restart handed to helper pid %s (jobs %s)", pid, ", ".join(str(j) for j in jobs))
        except Exception as e:
            logger.exception("Starting the Jarvis restart failed")
            with contextlib.suppress(OSError):
                path.unlink()
            msg = f"Neustart konnte nicht gestartet werden: {str(e) or type(e).__name__}"
            await self._set_self_restart(jobs, {"state": "failed", "message": msg}, warning=f"Jarvis-Neustart: {msg}")

    async def finish_self_restart(self, timeout_s: float = 600) -> None:
        """At core start: record the outcome of a restart this runner asked for in the jobs that caused it
        (``result.ship.self_restart``; failures also in ``result.warnings`` and as a notification). A helper that is
        still at work (health check, desktop app) is waited for."""
        path = self._restart_status_path()
        status = self_restart.read_status(path)
        if status is None:
            return
        deadline = asyncio.get_running_loop().time() + timeout_s
        while status.get("phase") in ("started", "running") and asyncio.get_running_loop().time() < deadline:
            helper = status.get("helper_pid")
            if status.get("phase") == "running" and isinstance(helper, int) and not self_restart.alive(helper):
                status = self_restart.read_status(path) or status  # it may have finished right before exiting
                break
            await asyncio.sleep(self.restart_status_poll_s)
            status = self_restart.read_status(path) or status
        phase = status.get("phase")
        info: dict[str, Any]
        if phase == "finished":
            outcome = status.get("outcome") or {}
            info = {
                "state": outcome.get("state") or "failed",
                "message": outcome.get("message") or "",
                "core": outcome.get("core"),
                "desktop": outcome.get("desktop"),
                "finished_at": status.get("finished_at"),
            }
        elif phase == "pending":
            info = {
                "state": "done",
                "message": "Der Core wurde vor dem geplanten Neustart anderweitig neu gestartet; der neue Stand ist "
                "geladen (Desktop-App nicht angefasst).",
            }
        else:
            info = {"state": "failed", "message": "Der Neustart-Helfer hat kein Ergebnis gemeldet (self-restart.log)."}
        jobs: list[uuid.UUID] = []
        for raw in status.get("jobs") or []:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                jobs.append(uuid.UUID(raw))
        warning = f"Jarvis-Neustart: {info['message']}" if info["state"] != "done" else None
        await self._set_self_restart(jobs, info, warning=warning)
        with contextlib.suppress(OSError):
            path.unlink()
        notifier = getattr(self.state, "notifier", None)
        if warning and notifier is not None:
            await notifier.notify(
                "coding.self_restart",
                "Jarvis-Neustart nach Coding-Job unvollständig",
                info["message"],
                level="warning",
                data={"job_ids": [str(j) for j in jobs], "self_restart": info},
            )

    async def _set_self_restart(
        self, job_ids: list[uuid.UUID], info: dict[str, Any], *, warning: str | None = None
    ) -> None:
        """Updates ``result.ship.self_restart`` of the jobs; ``warning`` is added to ``result.warnings``. The rest of
        the delivery result stays as it is."""
        for job_id in job_ids:
            async with session_scope() as s:
                job = await s.get(CodingJob, job_id)
                if job is None:
                    continue
                result = dict(job.result or {})
                ship = dict(result.get("ship") or {})
                ship["self_restart"] = {**(ship.get("self_restart") or {}), **info}
                result["ship"] = ship
                if warning:
                    result["warnings"] = [*(result.get("warnings") or []), warning]
                job.result = json.loads(json.dumps(result, default=str))
                status, title = job.status, job.title
            await bus.publish("coding.updated", job_id=str(job_id), status=status, title=title)

    async def prepare_pr(self, job_id: uuid.UUID, repo_full_name: str, base: str = "main") -> dict[str, Any]:
        """Manual opt-in: push the job branch and open a *draft* PR (not merged, nothing deployed)."""
        async with session_scope() as s:
            job = await s.get(CodingJob, job_id)
            if job is None or job.status != "completed":
                raise CodingError("Nur abgeschlossene Jobs können als PR vorbereitet werden")
            wt, branch, title, res = Path(job.worktree_path), job.branch, job.title, dict(job.result or {})
        if not wt.is_dir():
            raise CodingError("Worktree existiert nicht mehr (Job wurde bereits ausgeliefert und aufgeräumt)")
        gh = self._github()
        remotes = await self._git(wt, "remote")
        if "origin" not in remotes.split():
            raise CodingError("Repository hat kein 'origin'-Remote")
        await self._git(wt, "push", "-u", "origin", branch)
        pr = await gh.create_pr(repo_full_name, branch, base, f"Jarvis: {title}", self._pr_body(res)[:60000])
        return {"number": pr.get("number"), "url": pr.get("html_url")}
