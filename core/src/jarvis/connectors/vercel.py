"""Vercel über die REST API (``https://api.vercel.com``): Projekte anlegen, mit GitHub verbinden, deployen.

Werkzeuge: ``vercel_deploy`` (high, läuft über die Freigabe), ``vercel_list_projects`` und
``vercel_deployment_status`` (read). Der Token liegt im Keychain (``vercel-token``, alternativ
``JARVIS_SECRET_VERCEL_TOKEN`` oder ``VERCEL_TOKEN`` in der Umgebung des Core); die optionale Team-ID unter
``vercel-team-id`` bzw. ``VERCEL_TEAM_ID``. Der Token taucht weder in Logs noch in Fehlermeldungen oder Ergebnissen
auf; Werte von Umgebungsvariablen werden nie zurückgegeben (Freigabe-Text und Audit zeigen nur die Namen).

Quelle eines Deploys:
* **git** – das Projekt ist ein GitHub-Repo (``owner/name`` oder lokaler Checkout mit GitHub-``origin``, sauber und
  gepusht): Vercel baut den Branch direkt aus GitHub (``gitSource``); braucht die Vercel-GitHub-App für das Repo.
* **upload** – lokaler Ordner ohne GitHub bzw. mit lokalen, nicht gepushten Änderungen: die Dateien werden über
  ``/v2/files`` hochgeladen (``.gitignore`` wird beachtet, ``.env*``, ``.git``, ``node_modules`` und ``.vercel``
  nie) und Vercel baut sie.

Nach einem Merge ruft der Coding-Runner ``deploy_after_merge`` auf (siehe ``CodingRunner._ship_vercel``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from jarvis.coding.runner import parse_github_repo
from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

API = "https://api.vercel.com"
TOKEN_SECRET = "vercel-token"  # noqa: S105 - Keychain account name
TEAM_SECRET = "vercel-team-id"  # noqa: S105 - Keychain account name
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_TARGETS = ["production", "preview", "development"]
# Never uploaded: VCS, dependencies, local Vercel link, build caches and dotenv files (secrets go through ``env``).
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".vercel", ".next", ".turbo", ".cache", "__pycache__", ".venv", "venv"
}
_SKIP_FILES = {".DS_Store", "npm-debug.log"}
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 300 * 1024 * 1024
UPLOAD_PARALLEL = 8
POLL_INTERVAL_S = 5.0
DEPLOY_WAIT_S = 480.0
_DONE_STATES = {"READY", "ERROR", "CANCELED"}
_NO_GIT_ACCESS = (
    "Vercel hat keinen Zugriff auf {repo} ({error}). Vercel-GitHub-App für das Repo freigeben "
    "(https://github.com/apps/vercel) oder einen lokalen Projektordner angeben."
)
# package.json dependency → Vercel framework preset (first match wins; meta-frameworks before vite)
_FRAMEWORKS = (
    ("next", "nextjs"),
    ("nuxt", "nuxtjs"),
    ("@sveltejs/kit", "sveltekit-1"),
    ("astro", "astro"),
    ("@remix-run/dev", "remix"),
    ("gatsby", "gatsby"),
    ("react-scripts", "create-react-app"),
    ("@angular/core", "angular"),
    ("vite", "vite"),
)


class VercelError(ToolUserError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


def project_slug(text: str) -> str:
    """Vercel-Projektname: klein, nur a-z 0-9 . _ -, kein ``---``, höchstens 100 Zeichen."""
    s = re.sub(r"[^a-z0-9._-]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue"))
    s = re.sub(r"-{3,}", "--", s).strip("-._")
    return s[:100] or "jarvis-project"


def detect_framework(path: Path) -> str | None:
    """Framework-Preset für ein neues Projekt aus package.json; None = Vercel „Other“ (z. B. statisches HTML)."""
    try:
        pkg = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})} if isinstance(pkg, dict) else {}
    return next((preset for dep, preset in _FRAMEWORKS if dep in deps), None)


def read_link(path: Path) -> dict[str, Any]:
    """``.vercel/project.json`` der Vercel CLI (``projectId``, ``orgId``), falls vorhanden."""
    try:
        data = json.loads((path / ".vercel" / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def linked_repo(project: dict[str, Any]) -> str | None:
    link = project.get("link") or {}
    if link.get("type") != "github" or not link.get("org") or not link.get("repo"):
        return None
    return f"{link['org']}/{link['repo']}"


def _iso(ms: Any) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if isinstance(ms, int | float) else None


def _https(host: Any) -> str | None:
    return f"https://{host}" if host else None


async def _git(cwd: Path, *args: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_SECRET") and k != "VERCEL_TOKEN"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return (proc.returncode if proc.returncode is not None else 1), out.decode(errors="replace").strip()


@dataclass(slots=True)
class Source:
    path: Path | None = None  # local project directory
    repo: str | None = None  # GitHub owner/name
    root_dir: str = ""  # project directory relative to the repository root (monorepos)
    branch: str | None = None  # checked-out branch of the local copy
    pushed: bool = False  # local copy is clean and its branch is on origin → git deploy shows the same state
    notes: list[str] = field(default_factory=list)


class VercelConnector(Connector):
    manifest = Manifest(
        id="vercel",
        name="Vercel",
        description="Veröffentlicht lokale Projekte und GitHub-Repos auf Vercel (Projekt anlegen, mit GitHub "
        "verbinden, Umgebungsvariablen, Deploy) und zeigt Projekte und Deploy-Status.",
        category="developer",
        auth="token",
        secrets=[
            SecretField(
                TOKEN_SECRET,
                "Vercel Access Token",
                help="vercel.com › Account Settings › Tokens › Create Token (Scope: dein Account bzw. das Team).",
            ),
            SecretField(
                TEAM_SECRET,
                "Team-ID (optional)",
                help="Nur für Team-Projekte: vercel.com › Team › Settings › General › Team ID (beginnt mit team_).",
                required=False,
            ),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "deploy_after_merge": {
                    "type": "boolean",
                    "default": True,
                    "description": "Coding-Jobs nach dem Merge automatisch deployen, wenn das Projekt eine "
                    "vercel.json oder einen Vercel-Link (.vercel/project.json) hat",
                },
                "allowed_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "Weitere Ordner, aus denen lokale Projekte deployt werden dürfen "
                    "(Standard: Projekte und Coding-Workspaces)",
                },
            },
        },
        docs_url="https://vercel.com/docs/rest-api",
        setup_instructions="Token unter https://vercel.com/account/settings/tokens erstellen und hier hinterlegen "
        "(oder `jarvisctl secret set vercel-token`). Für GitHub-Deploys muss die Vercel-GitHub-App Zugriff auf das "
        "Repo haben (https://github.com/apps/vercel).",
        mcp_note="Vercel bietet einen offiziellen Remote-MCP-Server (OAuth, überwiegend lesend); Jarvis nutzt die "
        "REST API direkt, damit Deploys mit Risikostufe high über Freigabe und Audit laufen.",
    )

    # Tests setzen hier einen httpx.MockTransport ein; produktiv bleibt es beim Standard-Transport.
    transport: httpx.AsyncBaseTransport | None = None

    # ------------------------------------------------------------------ configuration
    def _token(self) -> str | None:
        return self.secret(TOKEN_SECRET) or os.environ.get("VERCEL_TOKEN") or None

    def _team_id(self) -> str | None:
        return self.secret(TEAM_SECRET) or os.environ.get("VERCEL_TEAM_ID") or None

    def missing_secrets(self) -> list[str]:
        return [] if self._token() else [TOKEN_SECRET]

    def _roots(self) -> list[Path]:
        p = self.state.settings.paths
        extra = [Path(r).expanduser() for r in self.settings.get("allowed_roots") or []]
        return [Path(d).resolve() for d in (p.projects_dir, p.workspaces_dir, *extra)]

    # ------------------------------------------------------------------ REST API
    async def _api(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, request_timeout: float = 30, **kw: Any
    ) -> Any:
        token = self._token()
        if not token:
            raise ToolUserError("Vercel ist nicht eingerichtet (Token 'vercel-token' fehlt).")
        query = dict(params or {})
        if team := self._team_id():
            query["teamId"] = team
        headers = {"Authorization": f"Bearer {token}", **kw.pop("headers", {})}
        try:
            async with httpx.AsyncClient(base_url=API, timeout=request_timeout, transport=self.transport) as c:
                r = await c.request(method, path, params=query, headers=headers, **kw)
        except httpx.HTTPError as e:  # message and type only – never the request (it carries the token)
            raise ToolUserError(f"Vercel nicht erreichbar ({type(e).__name__})") from None
        if r.status_code >= 400:
            try:
                err = (r.json() or {}).get("error") or {}
            except ValueError:
                err = {}
            msg = str(err.get("message") or r.reason_phrase or "")[:300]
            if r.status_code == 401:
                raise ToolUserError("Vercel lehnt den Token ab (401). Token prüfen oder neu erstellen.")
            raise VercelError(r.status_code, str(err.get("code") or ""), f"Vercel-Fehler {r.status_code}: {msg}")
        return r.json() if r.content else {}

    async def health(self) -> Health:
        try:
            user = (await self._api("GET", "/v2/user")).get("user") or {}
        except ToolUserError as e:
            return Health("error", str(e))
        team = " (Team)" if self._team_id() else ""
        return Health("ok", f"Angemeldet als {user.get('username') or user.get('email') or '?'}{team}")

    async def get_project(self, id_or_name: str) -> dict[str, Any] | None:
        try:
            return dict(await self._api("GET", f"/v9/projects/{id_or_name}"))
        except VercelError as e:
            if e.status == 404:
                return None
            raise

    async def list_projects(self, search: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if search:
            params["search"] = search
        data = await self._api("GET", "/v9/projects", params=params)
        out = []
        for p in data.get("projects") or []:
            latest = (p.get("latestDeployments") or [{}])[0]
            prod = (p.get("targets") or {}).get("production") or {}
            out.append(
                {
                    "name": p.get("name"),
                    "id": p.get("id"),
                    "framework": p.get("framework"),
                    "repo": linked_repo(p),
                    "production_url": _https((prod.get("alias") or [None])[0] or prod.get("url")),
                    "latest_deployment": {"state": latest.get("readyState"), "url": _https(latest.get("url"))}
                    if latest
                    else None,
                    "updated": _iso(p.get("updatedAt")),
                }
            )
        return out

    async def deployment(self, id_or_url: str) -> dict[str, Any]:
        ident = re.sub(r"^https?://", "", id_or_url.strip()).rstrip("/")
        return dict(await self._api("GET", f"/v13/deployments/{ident}"))

    @staticmethod
    def describe_deployment(d: dict[str, Any]) -> dict[str, Any]:
        state = d.get("readyState") or d.get("state") or d.get("status")
        out: dict[str, Any] = {
            "id": d.get("id") or d.get("uid"),
            "state": state,
            "target": d.get("target") or "preview",
            "url": _https(d.get("url")),
            "inspector_url": d.get("inspectorUrl"),
            "created": _iso(d.get("createdAt") or d.get("created")),
        }
        aliases = [a for a in d.get("alias") or [] if isinstance(a, str)]
        if aliases:
            out["aliases"] = [f"https://{a}" for a in aliases[:5]]
        meta = d.get("meta") or {}
        if meta.get("githubCommitRef"):
            out["git"] = {"ref": meta.get("githubCommitRef"), "sha": str(meta.get("githubCommitSha") or "")[:10]}
        if state == "ERROR":
            out["error"] = str(d.get("errorMessage") or d.get("errorCode") or "Build fehlgeschlagen")[:500]
        return out

    async def deployment_status(self, deployment: str | None, project: str | None, limit: int = 5) -> Any:
        if deployment:
            return self.describe_deployment(await self.deployment(deployment))
        if not project:
            raise ToolUserError("Bitte deployment (ID oder URL) oder project angeben.")
        proj = await self.get_project(project)
        if proj is None:
            raise ToolUserError(f"Vercel-Projekt '{project}' nicht gefunden.")
        data = await self._api("GET", "/v6/deployments", params={"projectId": proj["id"], "limit": min(limit, 20)})
        return {
            "project": proj.get("name"),
            "deployments": [self.describe_deployment(d) for d in data.get("deployments") or []],
        }

    # ------------------------------------------------------------------ deploy
    async def resolve_source(self, raw: str, *, trusted: bool = False) -> Source:
        """``owner/name`` bzw. GitHub-URL → Repo; absoluter Pfad (oder ``~``) → lokaler Ordner in erlaubten Wurzeln."""
        raw = raw.strip()
        if not raw.startswith(("/", "~")):
            repo = parse_github_repo(raw) or (raw if _REPO_RE.fullmatch(raw) else None)
            if repo is None:
                raise ToolUserError("project_path: absoluter Pfad eines lokalen Projekts oder GitHub-Repo owner/name.")
            return Source(repo=repo.removesuffix(".git"))
        path = Path(raw).expanduser().resolve()
        if not trusted and not any(path.is_relative_to(r) for r in self._roots()):
            raise ToolUserError(
                "Der Ordner liegt außerhalb der erlaubten Wurzeln (Projekte, Coding-Workspaces, "
                "Connector-Einstellung allowed_roots)."
            )
        if not path.is_dir():
            raise ToolUserError(f"Projektordner nicht gefunden: {path}")
        src = Source(path=path)
        code, top = await _git(path, "rev-parse", "--show-toplevel")
        if code != 0:
            return src
        _, origin = await _git(path, "remote", "get-url", "origin")
        src.repo = parse_github_repo(origin) if origin else None
        if src.repo is None:
            return src
        rel = path.relative_to(Path(top).resolve()) if path.is_relative_to(Path(top).resolve()) else Path()
        src.root_dir = "" if str(rel) == "." else rel.as_posix()
        _, src.branch = await _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        _, dirty = await _git(path, "status", "--porcelain")
        code, ahead = await _git(path, "rev-list", "--count", "@{u}..HEAD")
        src.pushed = not dirty and code == 0 and ahead == "0" and src.branch != "HEAD"
        return src

    def collect_files(self, root: Path, listed: list[str] | None) -> list[tuple[str, Path]]:
        """Dateien für einen Upload-Deploy (relativer Pfad, absoluter Pfad); ``listed`` = ``git ls-files``."""
        if listed is None:
            listed = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                base = Path(dirpath).relative_to(root)
                listed.extend((base / f).as_posix() for f in filenames)
        files: list[tuple[str, Path]] = []
        total = 0
        for rel in sorted(set(listed)):
            parts = rel.split("/")
            if any(p in _SKIP_DIRS for p in parts[:-1]) or parts[-1] in _SKIP_FILES or parts[-1].startswith(".env"):
                continue
            path = root / rel
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            files.append((rel, path))
            if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ToolUserError(
                    f"Projekt zu groß für einen Upload-Deploy (max. {MAX_FILES} Dateien / "
                    f"{MAX_TOTAL_BYTES // 1024 // 1024} MB). Über GitHub deployen oder .gitignore prüfen."
                )
        if not files:
            raise ToolUserError("Keine Dateien zum Hochladen gefunden.")
        return files

    async def _upload(self, root: Path) -> list[dict[str, Any]]:
        code, out = await _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", ".")
        listed = [p for p in out.split("\0") if p] if code == 0 else None
        files = self.collect_files(root, listed)
        sem = asyncio.Semaphore(UPLOAD_PARALLEL)

        async def one(rel: str, path: Path) -> dict[str, Any]:
            data = path.read_bytes()
            sha = hashlib.sha1(data, usedforsecurity=False).hexdigest()
            async with sem:
                await self._api(
                    "POST",
                    "/v2/files",
                    content=data,
                    request_timeout=120,
                    headers={"Content-Type": "application/octet-stream", "x-vercel-digest": sha},
                )
            return {"file": rel, "sha": sha, "size": len(data)}

        return list(await asyncio.gather(*(one(rel, path) for rel, path in files)))

    async def _ensure_project(
        self, name: str, src: Source, notes: list[str], *, explicit: bool
    ) -> tuple[dict[str, Any], bool]:
        """Holt das Projekt oder legt es an (mit GitHub-Verbindung, falls möglich). → (Projekt, neu angelegt).

        ``explicit`` = Name von Sir bzw. aus ``.vercel/project.json``; ein nur abgeleiteter Name darf kein Projekt
        treffen, das mit einem anderen Repo verbunden ist (sonst würde fremder Code dessen Produktion überschreiben).
        """
        project = await self.get_project(name)
        if project is not None:
            current = linked_repo(project)
            if current and (src.repo or "").lower() != current.lower() and not explicit:
                raise ToolUserError(
                    f"Das Vercel-Projekt '{project.get('name')}' gehört zu {current}. "
                    "Bitte project_name ausdrücklich angeben, um es trotzdem zu verwenden."
                )
            if src.repo and current is None:
                try:
                    link = {"type": "github", "repo": src.repo}
                    await self._api("POST", f"/v9/projects/{project['id']}/link", json=link)
                    project = await self.get_project(project["id"]) or project
                    notes.append(f"Projekt mit GitHub-Repo {src.repo} verbunden.")
                except VercelError as e:
                    notes.append(f"Verbindung mit GitHub nicht möglich ({e}); Vercel-GitHub-App für das Repo prüfen.")
            elif src.repo and current and current.lower() != src.repo.lower():
                notes.append(f"Projekt ist mit {current} verbunden, nicht mit {src.repo} – Verbindung unverändert.")
            return project, False
        body: dict[str, Any] = {"name": name}
        if src.path is not None and (framework := detect_framework(src.path)):
            body["framework"] = framework
        if src.root_dir:
            body["rootDirectory"] = src.root_dir
        if src.repo:
            try:
                git = {"gitRepository": {"type": "github", "repo": src.repo}}
                created = await self._api("POST", "/v11/projects", json={**body, **git})
                notes.append(f"Vercel-Projekt {name} angelegt und mit {src.repo} verbunden.")
                return dict(created), True
            except VercelError as e:
                if e.status not in (400, 403, 404):
                    raise
                if src.path is None:  # nothing local to upload: do not leave an empty project behind
                    raise ToolUserError(_NO_GIT_ACCESS.format(repo=src.repo, error=e)) from None
                notes.append(f"Verbindung mit GitHub nicht möglich ({e}); Vercel-GitHub-App für das Repo prüfen.")
        created = await self._api("POST", "/v11/projects", json=body)
        notes.append(f"Vercel-Projekt {name} angelegt.")
        return dict(created), True

    async def _set_env(self, project_id: str, env: dict[str, str], targets: list[str]) -> list[str]:
        items = []
        for key, value in env.items():
            if not _ENV_KEY_RE.fullmatch(key):
                raise ToolUserError(f"Ungültiger Name für eine Umgebungsvariable: {key}")
            if value.startswith("secret:"):  # value comes from Jarvis' Keychain, never through the chat
                value = self.secret(value.removeprefix("secret:")) or ""
                if not value:
                    raise ToolUserError(f"Secret für {key} ist im Schlüsselbund nicht gesetzt.")
            items.append({"key": key, "value": value, "type": "encrypted", "target": targets})
        if items:
            await self._api("POST", f"/v10/projects/{project_id}/env", params={"upsert": "true"}, json=items)
        return sorted(env)

    async def _wait(self, deployment: dict[str, Any], wait_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + wait_s
        while (deployment.get("readyState") or deployment.get("status")) not in _DONE_STATES:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                deployment = await self.deployment(str(deployment["id"]))
            except ToolUserError as e:  # short network hiccup: the build keeps running at Vercel
                logger.debug("Vercel status poll failed: %s", e)
        return deployment

    async def deploy(
        self,
        raw_path: str,
        *,
        production: bool = True,
        project_name: str | None = None,
        env: dict[str, str] | None = None,
        env_targets: list[str] | None = None,
        source: str = "auto",
        ref: str | None = None,
        wait_s: float = DEPLOY_WAIT_S,
        src: Source | None = None,
    ) -> dict[str, Any]:
        src = src or await self.resolve_source(raw_path)
        notes = src.notes
        link = read_link(src.path) if src.path else {}
        name = project_slug(project_name) if project_name else None
        if name is None and link.get("projectId") and await self.get_project(str(link["projectId"])):
            name = str(link["projectId"])
        explicit = name is not None
        if name is None:
            name = project_slug(src.path.name if src.path else str(src.repo).split("/")[-1])

        if source == "git" and not src.repo:
            raise ToolUserError("Für source=git braucht es ein GitHub-Repo (origin auf github.com).")
        if source == "upload" and src.path is None:
            raise ToolUserError("Für source=upload braucht es einen lokalen Projektordner.")
        use_git = source == "git" or (source == "auto" and src.repo is not None and (src.path is None or src.pushed))
        if source == "auto" and src.repo and src.path is not None and not src.pushed:
            notes.append("Lokale Änderungen sind nicht (vollständig) gepusht – deploye den lokalen Stand per Upload.")

        project, created = await self._ensure_project(name, src, notes, explicit=explicit)
        env_keys = await self._set_env(project["id"], env or {}, env_targets or ["production", "preview"])
        if use_git and linked_repo(project) is None:
            if src.path is None:
                raise ToolUserError(_NO_GIT_ACCESS.format(repo=src.repo, error="Projekt nicht verbunden"))
            use_git = False
            notes.append("Deploy per Upload, weil das Projekt nicht mit GitHub verbunden ist.")

        body: dict[str, Any] = {"name": project["name"], "project": project["id"]}
        if production:
            body["target"] = "production"
        if use_git:
            org, repo = str(src.repo).split("/", 1)
            branch = ref or (src.branch if src.path is not None else None)
            branch = branch or ((project.get("link") or {}).get("productionBranch")) or "main"
            body["gitSource"] = {"type": "github", "org": org, "repo": repo, "ref": branch}
        else:
            assert src.path is not None
            body["files"] = await self._upload(src.path)
        params = {"skipAutoDetectionConfirmation": "1", "forceNew": "1"}
        deployment = await self._api("POST", "/v13/deployments", params=params, json=body, request_timeout=120)
        if wait_s > 0:
            deployment = await self._wait(deployment, wait_s)
        info = self.describe_deployment(deployment)
        state = info["state"]
        result: dict[str, Any] = {
            "project": project.get("name"),
            "project_id": project.get("id"),
            "project_created": created,
            "repo": linked_repo(project) or src.repo,
            "source": f"github:{body['gitSource']['ref']}" if use_git else f"upload ({len(body['files'])} Dateien)",
            "target": "production" if production else "preview",
            "deployment_id": info["id"],
            "state": state,
            "url": info["url"],
            "inspector_url": info["inspector_url"],
        }
        if production and info.get("aliases"):
            result["production_url"] = info["aliases"][0]
        if env_keys:
            result["env_set"] = env_keys
        if info.get("error"):
            result["error"] = info["error"]
        if state not in _DONE_STATES:
            notes.append("Build läuft noch – Stand mit vercel_deployment_status abfragen.")
        if notes:
            result["notes"] = notes
        return result

    async def deploy_after_merge(self, repo_path: Path, repo_name: str, base: str) -> dict[str, Any]:
        """Vom Coding-Runner nach dem Merge nach ``base``: produktiv aus GitHub deployen. Ist das Projekt bereits mit
        diesem Repo und Produktions-Branch verbunden, baut Vercel selbst (Git-Integration) – kein zweiter Deploy."""
        link = read_link(repo_path)
        project = await self.get_project(str(link["projectId"])) if link.get("projectId") else None
        project = project or await self.get_project(project_slug(repo_path.name))
        if project is not None and (linked_repo(project) or "").lower() == repo_name.lower():
            if ((project.get("link") or {}).get("productionBranch") or "main") == base:
                return {
                    "state": "git_integration",
                    "project": project.get("name"),
                    "message": f"Vercel deployt {project.get('name')} selbst über die Git-Integration.",
                }
        # deploy() picks the project again (link file or folder name) with its guard against foreign repos
        src = Source(path=repo_path, repo=repo_name, branch=base, pushed=True)
        result = await self.deploy(str(repo_path), production=True, source="git", ref=base, src=src)
        url = result.get("production_url") or result.get("url")
        done = {"READY": f"deployt: {url}", "ERROR": f"Build fehlgeschlagen ({result.get('error', '')})"}
        result["message"] = f"Vercel {result['project']} " + done.get(
            str(result["state"]), f"gestartet ({result['state']}): {url}"
        )
        return result

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[ToolSpec]:
        S = "vercel"

        async def deploy_tool(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.deploy(
                a["project_path"],
                production=bool(a.get("production", True)),
                project_name=a.get("project_name"),
                env=a.get("env"),
                env_targets=a.get("env_targets"),
                source=a.get("source", "auto"),
                ref=a.get("ref"),
            )

        async def projects_tool(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.list_projects(a.get("search"), int(a.get("limit", 20)))

        async def status_tool(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.deployment_status(a.get("deployment"), a.get("project"), int(a.get("limit", 5)))

        def deploy_summary(a: dict[str, Any]) -> str:
            target = "Produktion" if a.get("production", True) else "Vorschau"
            name = f" als {a['project_name']}" if a.get("project_name") else ""
            env = f"; setzt Umgebungsvariablen: {', '.join(sorted(a['env']))}" if a.get("env") else ""
            return f"Vercel-Deploy ({target}): {a['project_path']}{name}{env}"

        return [
            ToolSpec(
                "vercel_deploy",
                "Veröffentlicht ein lokales Projekt (absoluter Pfad) oder ein GitHub-Repo (owner/name) auf Vercel: "
                "legt das Vercel-Projekt bei Bedarf an, verbindet es mit dem GitHub-Repo, setzt optional "
                "Umgebungsvariablen, deployt und gibt die URL zurück. Wartet bis zu 8 Minuten auf den Build.",
                _obj(
                    {
                        "project_path": {
                            "type": "string",
                            "minLength": 3,
                            "description": "Absoluter Pfad des Projektordners oder GitHub-Repo owner/name",
                        },
                        "production": {"type": "boolean", "default": True, "description": "false = Vorschau-Deploy"},
                        "project_name": {
                            "type": "string",
                            "maxLength": 100,
                            "description": "Vercel-Projektname (Standard: Ordner- bzw. Repo-Name)",
                        },
                        "env": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "maxProperties": 50,
                            "description": "Umgebungsvariablen NAME → Wert; 'secret:<name>' liest den Wert aus "
                            "Jarvis' Schlüsselbund (bevorzugt für geheime Werte)",
                        },
                        "env_targets": {
                            "type": "array",
                            "items": {"type": "string", "enum": ENV_TARGETS},
                            "minItems": 1,
                            "description": "Umgebungen für env (Standard: production und preview)",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "git", "upload"],
                            "default": "auto",
                            "description": "auto: aus GitHub, wenn der lokale Stand gepusht ist, sonst Upload",
                        },
                        "ref": {"type": "string", "description": "Git-Branch für Deploys aus GitHub"},
                    },
                    ["project_path"],
                ),
                deploy_tool,
                "high",
                S,
                writes=True,
                timeout_s=DEPLOY_WAIT_S + 300,  # + upload; stays below the chat turn timeout
                summarize=deploy_summary,
            ),
            ToolSpec(
                "vercel_list_projects",
                "Listet Sirs Vercel-Projekte mit verbundenem Repo, Produktions-URL und letztem Deploy.",
                _obj(
                    {
                        "search": {"type": "string", "description": "Filter nach Projektname"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    }
                ),
                projects_tool,
                "read",
                S,
            ),
            ToolSpec(
                "vercel_deployment_status",
                "Status eines Vercel-Deploys (ID oder URL) oder die letzten Deploys eines Projekts "
                "(Zustand, URL, Fehlermeldung).",
                _obj(
                    {
                        "deployment": {"type": "string", "description": "Deployment-ID (dpl_…) oder URL"},
                        "project": {"type": "string", "description": "Projektname oder -ID"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    }
                ),
                status_tool,
                "read",
                S,
            ),
        ]
