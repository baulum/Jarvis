"""Vercel connector against a mocked REST API (httpx.MockTransport): project creation and GitHub link, env vars,
git and upload deploys, status, and that neither the token nor env values leak."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jarvis.connectors import vercel as vm
from jarvis.connectors.vercel import VercelConnector, detect_framework, project_slug
from jarvis.tools.executor import ToolUserError, audit_args

TOKEN = "vcp_super_secret_token"


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeVercel:
    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.env: list[tuple[dict[str, str], Any]] = []
        self.uploads: dict[str, bytes] = {}
        self.deployments: list[dict[str, Any]] = []
        self.auth: set[str | None] = set()
        self.teams: set[str | None] = set()
        self.polls = 0
        self.git_access = True
        self.final_state = "READY"

    def add_project(self, name: str, repo: str | None = None, branch: str = "main") -> dict[str, Any]:
        p: dict[str, Any] = {"id": f"prj_{len(self.projects) + 1}", "name": name, "updatedAt": 1_700_000_000_000}
        if repo:
            org, r = repo.split("/")
            p["link"] = {"type": "github", "org": org, "repo": r, "productionBranch": branch}
        self.projects[name] = p
        return p

    def _find(self, key: str) -> dict[str, Any] | None:
        return next((p for p in self.projects.values() if key in (p["id"], p["name"])), None)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.add(req.headers.get("authorization"))
        self.teams.add(req.url.params.get("teamId"))
        path, method = req.url.path, req.method
        body = json.loads(req.content) if req.content and path != "/v2/files" else None
        if method == "GET" and path == "/v2/user":
            return httpx.Response(200, json={"user": {"username": "paul"}})
        if method == "GET" and path == "/v9/projects":
            return httpx.Response(200, json={"projects": list(self.projects.values())})
        if method == "GET" and path.startswith("/v9/projects/"):
            p = self._find(path.split("/")[3])
            if p is None:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": "Project not found"}})
            return httpx.Response(200, json=p)
        if method == "POST" and path == "/v11/projects":
            assert body is not None
            if "gitRepository" in body and not self.git_access:
                return httpx.Response(400, json={"error": {"code": "bad_request", "message": "Install the GitHub integration first."}})
            self.created.append(body)
            p = self.add_project(body["name"], (body.get("gitRepository") or {}).get("repo"))
            p["framework"] = body.get("framework")
            return httpx.Response(200, json=p)
        if method == "POST" and path.endswith("/link"):
            if not self.git_access:
                return httpx.Response(400, json={"error": {"message": "Install the GitHub integration first."}})
            self.links.append(body or {})
            p = self._find(path.split("/")[3])
            assert p is not None and body is not None
            org, r = body["repo"].split("/")
            p["link"] = {"type": "github", "org": org, "repo": r, "productionBranch": "main"}
            return httpx.Response(200, json=p)
        if method == "POST" and path.endswith("/env"):
            self.env.append((dict(req.url.params), body))
            return httpx.Response(201, json={"created": []})
        if method == "POST" and path == "/v2/files":
            sha = req.headers["x-vercel-digest"]
            assert hashlib.sha1(req.content).hexdigest() == sha  # what Vercel checks against x-vercel-digest
            self.uploads[sha] = req.content
            return httpx.Response(200, json={})
        if method == "POST" and path == "/v13/deployments":
            assert body is not None
            self.deployments.append({"params": dict(req.url.params), "body": body})
            return httpx.Response(200, json={"id": "dpl_1", "url": "app-abc.vercel.app", "readyState": "QUEUED"})
        if method == "GET" and path in ("/v13/deployments/dpl_1", "/v13/deployments/app-abc.vercel.app"):
            self.polls += 1
            state = "BUILDING" if self.polls == 1 else self.final_state
            prod = self.deployments[-1]["body"].get("target") == "production"
            return httpx.Response(
                200,
                json={
                    "id": "dpl_1",
                    "url": "app-abc.vercel.app",
                    "readyState": state,
                    "target": "production" if prod else None,
                    "alias": ["app.vercel.app"] if prod and state == "READY" else [],
                    "inspectorUrl": "https://vercel.com/paul/app/dpl_1",
                    "errorMessage": "Command \"npm run build\" exited with 1" if state == "ERROR" else None,
                },
            )
        if method == "GET" and path == "/v6/deployments":
            return httpx.Response(
                200,
                json={
                    "deployments": [
                        {
                            "uid": "dpl_9",
                            "url": "app-9.vercel.app",
                            "state": "READY",
                            "created": 1_700_000_000_000,
                            "target": "production",
                            "meta": {"githubCommitRef": "main", "githubCommitSha": "abcdef1234567"},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": f"unknown {path}"}})


def _state(tmp_path: Path) -> SimpleNamespace:
    paths = SimpleNamespace(projects_dir=tmp_path / "projects", workspaces_dir=tmp_path / "ws")
    paths.projects_dir.mkdir()
    paths.workspaces_dir.mkdir()
    return SimpleNamespace(settings=SimpleNamespace(paths=paths))


def _connector(tmp_path: Path, fake: FakeVercel, team: str | None = None) -> VercelConnector:
    conn = VercelConnector(_state(tmp_path))  # type: ignore[arg-type]
    conn.transport = httpx.MockTransport(fake)
    secrets = {"vercel-token": TOKEN, "vercel-team-id": team, "db-password": "pw-from-keychain"}
    conn.secret = lambda name: secrets.get(name)  # type: ignore[method-assign]
    return conn


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vm, "POLL_INTERVAL_S", 0.0)
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)


def _site(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"dependencies": {"next": "15", "react": "19"}}))
    (root / "app").mkdir()
    (root / "app" / "page.tsx").write_text("export default function P() { return null }\n")
    (root / ".env.local").write_text("SECRET=1\n")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "index.js").write_text("x\n")
    return root


# ---------------------------------------------------------------------------------------------- registry
def test_tools_registered_with_expected_risks(tmp_path: Path) -> None:
    specs = {s.name: s for s in VercelConnector(_state(tmp_path)).tools()}  # type: ignore[arg-type]
    assert set(specs) == {"vercel_deploy", "vercel_list_projects", "vercel_deployment_status"}
    deploy = specs["vercel_deploy"]
    assert deploy.risk == "high" and deploy.writes and deploy.connector == "vercel"
    assert deploy.timeout_s < 900  # stays below the chat turn timeout
    assert specs["vercel_list_projects"].risk == "read" and specs["vercel_deployment_status"].risk == "read"
    assert set(deploy.parameters["properties"]) >= {"project_path", "production", "project_name", "env"}
    summary = deploy.describe({"project_path": "acme/app", "production": False, "env": {"API_KEY": "geheim"}})
    assert "Vorschau" in summary and "API_KEY" in summary and "geheim" not in summary


def test_audit_log_keeps_only_env_names() -> None:
    logged = audit_args("vercel_deploy", {"project_path": "acme/app", "env": {"API_KEY": "geheim123"}})
    assert logged == {"project_path": "acme/app", "env": {"API_KEY": "[9 Zeichen]"}}


def test_token_from_keychain_or_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = VercelConnector(_state(tmp_path))  # type: ignore[arg-type]
    conn.secret = lambda name: None  # type: ignore[method-assign]
    assert conn.missing_secrets() == ["vercel-token"]  # the team id is optional
    monkeypatch.setenv("VERCEL_TOKEN", "from-env")
    assert conn.missing_secrets() == [] and conn._token() == "from-env"


def test_helpers() -> None:
    assert project_slug("Mein Café  Projekt!!") == "mein-caf-projekt"
    assert project_slug("Über---Uns") == "ueber--uns"
    assert project_slug("___") == "jarvis-project"


def test_detect_framework(tmp_path: Path) -> None:
    assert detect_framework(tmp_path) is None
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"vite": "6", "astro": "5"}}))
    assert detect_framework(tmp_path) == "astro"  # meta-framework wins over vite


# ---------------------------------------------------------------------------------------------- deploy
async def test_github_repo_creates_linked_project_and_deploys_production(tmp_path: Path) -> None:
    fake = FakeVercel()
    out = await _connector(tmp_path, fake, team="team_abc").deploy("acme/shop", production=True)

    assert fake.created == [{"name": "shop", "gitRepository": {"type": "github", "repo": "acme/shop"}}]
    dep = fake.deployments[0]
    assert dep["body"]["gitSource"] == {"type": "github", "org": "acme", "repo": "shop", "ref": "main"}
    assert dep["body"]["target"] == "production" and dep["params"]["skipAutoDetectionConfirmation"] == "1"
    assert out["state"] == "READY" and out["production_url"] == "https://app.vercel.app"
    assert out["url"] == "https://app-abc.vercel.app" and out["project_created"] is True
    assert out["repo"] == "acme/shop" and out["source"] == "github:main"
    assert fake.auth == {f"Bearer {TOKEN}"} and fake.teams == {"team_abc"}
    assert TOKEN not in json.dumps(out)


async def test_local_folder_is_uploaded_without_secrets_and_env_is_set(tmp_path: Path) -> None:
    fake = FakeVercel()
    conn = _connector(tmp_path, fake)
    site = _site(conn.state.settings.paths.projects_dir / "Mein Shop")
    out = await conn.deploy(
        str(site), production=False, env={"API_URL": "https://api.example", "DB_PASSWORD": "secret:db-password"}
    )

    assert fake.created == [{"name": "mein-shop", "framework": "nextjs"}]
    files = {f["file"] for f in fake.deployments[0]["body"]["files"]}
    assert files == {"package.json", "app/page.tsx"}  # no .env*, no node_modules
    assert "target" not in fake.deployments[0]["body"]  # preview
    assert len(fake.uploads) == 2
    params, items = fake.env[0]
    assert params["upsert"] == "true"
    assert {i["key"]: i["value"] for i in items} == {"API_URL": "https://api.example", "DB_PASSWORD": "pw-from-keychain"}
    assert all(i["type"] == "encrypted" and i["target"] == ["production", "preview"] for i in items)
    assert out["env_set"] == ["API_URL", "DB_PASSWORD"] and "pw-from-keychain" not in json.dumps(out)
    assert out["target"] == "preview" and out["source"] == "upload (2 Dateien)" and "production_url" not in out


async def test_local_git_checkout_deploys_from_github_when_pushed_else_uploads(tmp_path: Path) -> None:
    fake = FakeVercel()
    conn = _connector(tmp_path, fake)
    origin = tmp_path / "github.com" / "acme" / "site.git"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "--bare", "-b", "main")
    repo = _site(conn.state.settings.paths.projects_dir / "site")
    (repo / ".gitignore").write_text("node_modules\n.env*\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", origin.as_posix())  # URL form ".../github.com/acme/site.git" on every OS
    _git(repo, "push", "-q", "-u", "origin", "main")

    out = await conn.deploy(str(repo))
    assert fake.deployments[0]["body"]["gitSource"]["ref"] == "main" and out["source"] == "github:main"

    (repo / "app" / "page.tsx").write_text("changed\n")  # not pushed: the local state must be what goes live
    out = await conn.deploy(str(repo))
    body = fake.deployments[1]["body"]
    assert "gitSource" not in body and {f["file"] for f in body["files"]} == {".gitignore", "package.json", "app/page.tsx"}
    assert any("nicht (vollständig) gepusht" in n for n in out["notes"])
    assert len(fake.created) == 1  # the project from the first deploy is reused


async def test_existing_unlinked_project_gets_linked(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.add_project("shop")
    out = await _connector(tmp_path, fake).deploy("acme/shop")
    assert fake.links == [{"type": "github", "repo": "acme/shop"}] and not fake.created
    assert out["repo"] == "acme/shop" and any("verbunden" in n for n in out["notes"])


async def test_repo_without_vercel_github_access_is_explained(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.git_access = False
    with pytest.raises(ToolUserError, match="GitHub-App"):
        await _connector(tmp_path, fake).deploy("acme/shop")
    assert not fake.deployments and not fake.created  # no empty project left behind


async def test_derived_name_never_hits_a_project_of_another_repo(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.add_project("shop", repo="someone/else")
    conn = _connector(tmp_path, fake)
    with pytest.raises(ToolUserError, match="gehört zu someone/else"):
        await conn.deploy("acme/shop")
    assert not fake.deployments


async def test_path_outside_allowed_roots_is_refused_before_any_request(tmp_path: Path) -> None:
    fake = FakeVercel()
    conn = _connector(tmp_path, fake)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ToolUserError, match="außerhalb"):
        await conn.deploy(str(outside))
    with pytest.raises(ToolUserError, match="außerhalb"):
        await conn.deploy(str(conn.state.settings.paths.projects_dir / ".." / "elsewhere"))
    with pytest.raises(ToolUserError):
        await conn.deploy("kein repo")
    assert fake.auth == set()


async def test_failed_build_reports_vercel_error(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.final_state = "ERROR"
    out = await _connector(tmp_path, fake).deploy("acme/shop")
    assert out["state"] == "ERROR" and "npm run build" in out["error"] and "production_url" not in out


async def test_api_errors_never_contain_the_token(tmp_path: Path) -> None:
    def deny(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"bad token {req.headers['authorization']}"}})

    conn = _connector(tmp_path, FakeVercel())
    conn.transport = httpx.MockTransport(deny)
    with pytest.raises(ToolUserError) as e:
        await conn.list_projects()
    assert "401" in str(e.value) and TOKEN not in str(e.value)

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {req.headers['authorization']}")

    conn.transport = httpx.MockTransport(down)
    with pytest.raises(ToolUserError) as e:
        await conn.list_projects()
    assert "ConnectError" in str(e.value) and TOKEN not in str(e.value)
    health = await conn.health()
    assert health.status == "error" and TOKEN not in health.message


# ---------------------------------------------------------------------------------------------- read tools
async def test_list_projects_and_status(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.add_project("shop", repo="acme/shop")
    conn = _connector(tmp_path, fake)
    projects = await conn.list_projects()
    assert projects[0]["name"] == "shop" and projects[0]["repo"] == "acme/shop" and projects[0]["updated"]

    status = await conn.deployment_status(None, "shop")
    dep = status["deployments"][0]
    assert dep["id"] == "dpl_9" and dep["state"] == "READY" and dep["url"] == "https://app-9.vercel.app"
    assert dep["git"] == {"ref": "main", "sha": "abcdef1234"}

    fake.deployments.append({"body": {"target": "production"}})
    fake.polls = 1
    single = await conn.deployment_status("https://app-abc.vercel.app/", None)
    assert single["state"] == "READY" and single["aliases"] == ["https://app.vercel.app"]
    with pytest.raises(ToolUserError):
        await conn.deployment_status(None, None)


# ---------------------------------------------------------------------------------------------- after merge
async def test_after_merge_leaves_git_linked_projects_to_vercel(tmp_path: Path) -> None:
    fake = FakeVercel()
    fake.add_project("app", repo="acme/app")
    repo = tmp_path / "app"
    repo.mkdir()
    out = await _connector(tmp_path, fake).deploy_after_merge(repo, "acme/app", "main")
    assert out["state"] == "git_integration" and not fake.deployments


async def test_after_merge_deploys_production_from_github(tmp_path: Path) -> None:
    fake = FakeVercel()
    repo = tmp_path / "app"
    (repo / ".vercel").mkdir(parents=True)
    linked = fake.add_project("marketing-site")
    (repo / ".vercel" / "project.json").write_text(json.dumps({"projectId": linked["id"], "orgId": "team_x"}))
    out = await _connector(tmp_path, fake).deploy_after_merge(repo, "acme/app", "main")

    assert fake.links == [{"type": "github", "repo": "acme/app"}]  # the linked project, not one named "app"
    body = fake.deployments[0]["body"]
    assert body["project"] == linked["id"] and body["target"] == "production"
    assert body["gitSource"]["ref"] == "main"
    assert out["state"] == "READY" and "https://app.vercel.app" in out["message"]
