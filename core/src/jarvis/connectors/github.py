"""GitHub via REST API v3 (fine-grained personal access token).

The official GitHub MCP server (https://api.githubcopilot.com/mcp/) exists and can be added
in ``config/mcp_servers.yaml``; it exposes ~50 tools, which overwhelms a 4B local model, so
Jarvis ships this focused connector for the core workflows.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

API = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class GitHubConnector(Connector):
    manifest = Manifest(
        id="github",
        name="GitHub",
        description="Repositories, Branches, Commits, Pull Requests und Issues.",
        category="developer",
        auth="token",
        secrets=[
            SecretField(
                "github-token",
                "Fine-grained Personal Access Token",
                help="github.com › Settings › Developer settings › Fine-grained tokens. Rechte: Contents "
                "(read/write), Pull requests (read/write), Issues (read/write), Metadata (read).",
            )
        ],
        mcp_note="Offizieller GitHub-MCP-Server verfügbar (optional in config/mcp_servers.yaml); für das lokale "
        "4B-Modell nutzt Jarvis standardmäßig diesen fokussierten Connector.",
    )

    async def _api(self, method: str, path: str, **kw: Any) -> Any:
        token = self.secret("github-token")
        if not token:
            raise ToolUserError("GitHub ist nicht eingerichtet (Token fehlt).")
        async with httpx.AsyncClient(base_url=API, timeout=30) as c:
            r = await c.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                **kw,
            )
        if r.status_code >= 400:
            raise ToolUserError(f"GitHub-Fehler {r.status_code}: {r.json().get('message', r.text[:200])}")
        return r.json() if r.content else {}

    async def health(self) -> Health:
        try:
            me = await self._api("GET", "/user")
            return Health("ok", f"Angemeldet als {me.get('login')}")
        except ToolUserError as e:
            return Health("error", str(e))

    async def create_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool = True) -> Any:
        return await self._api(
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        )

    def tools(self) -> list[ToolSpec]:
        S = "github"
        repo_prop = {"repo": {"type": "string", "pattern": _REPO_RE.pattern, "description": "owner/name"}}

        async def repos(ctx: ToolContext, a: dict[str, Any]) -> Any:
            data = await self._api("GET", "/user/repos", params={"sort": "updated", "per_page": 30})
            return [
                {
                    "repo": r["full_name"],
                    "private": r["private"],
                    "default_branch": r["default_branch"],
                    "updated": r["updated_at"],
                }
                for r in data
            ]

        async def branches(ctx: ToolContext, a: dict[str, Any]) -> Any:
            data = await self._api("GET", f"/repos/{a['repo']}/branches", params={"per_page": 50})
            return [b["name"] for b in data]

        async def commits(ctx: ToolContext, a: dict[str, Any]) -> Any:
            params = {"per_page": min(int(a.get("limit", 10)), 30)}
            if a.get("branch"):
                params["sha"] = a["branch"]  # type: ignore[assignment]
            data = await self._api("GET", f"/repos/{a['repo']}/commits", params=params)
            return [
                {
                    "sha": c["sha"][:10],
                    "message": c["commit"]["message"].split("\n")[0],
                    "author": c["commit"]["author"]["name"],
                    "date": c["commit"]["author"]["date"],
                }
                for c in data
            ]

        async def prs(ctx: ToolContext, a: dict[str, Any]) -> Any:
            data = await self._api("GET", f"/repos/{a['repo']}/pulls", params={"state": a.get("state", "open")})
            return [
                {
                    "number": p["number"],
                    "title": p["title"],
                    "head": p["head"]["ref"],
                    "draft": p["draft"],
                    "url": p["html_url"],
                }
                for p in data
            ]

        async def create_pr_tool(ctx: ToolContext, a: dict[str, Any]) -> Any:
            p = await self.create_pr(
                a["repo"], a["head"], a.get("base", "main"), a["title"], a.get("body", ""), a.get("draft", True)
            )
            return {"number": p["number"], "url": p["html_url"]}

        async def issues(ctx: ToolContext, a: dict[str, Any]) -> Any:
            data = await self._api(
                "GET", f"/repos/{a['repo']}/issues", params={"state": a.get("state", "open"), "per_page": 20}
            )
            listing = "\n".join(f"#{i['number']} {i['title']}" for i in data if "pull_request" not in i)
            return wrap_untrusted(listing or "(keine Issues)", f"github:{a['repo']}", 6000)

        async def create_issue(ctx: ToolContext, a: dict[str, Any]) -> Any:
            i = await self._api(
                "POST", f"/repos/{a['repo']}/issues", json={"title": a["title"], "body": a.get("body", "")}
            )
            return {"number": i["number"], "url": i["html_url"]}

        return [
            ToolSpec("github_list_repos", "Listet Sirs Repositories.", _obj({}), repos, "read", S),
            ToolSpec("github_list_branches", "Branches eines Repos.", _obj(repo_prop, ["repo"]), branches, "read", S),
            ToolSpec(
                "github_list_commits",
                "Letzte Commits.",
                _obj({**repo_prop, "branch": {"type": "string"}, "limit": {"type": "integer"}}, ["repo"]),
                commits,
                "read",
                S,
            ),
            ToolSpec(
                "github_list_prs",
                "Pull Requests.",
                _obj({**repo_prop, "state": {"type": "string", "enum": ["open", "closed", "all"]}}, ["repo"]),
                prs,
                "read",
                S,
            ),
            ToolSpec(
                "github_create_pr",
                "Erstellt einen Pull Request (standardmäßig als Entwurf).",
                _obj(
                    {
                        **repo_prop,
                        "head": {"type": "string"},
                        "base": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "draft": {"type": "boolean"},
                    },
                    ["repo", "head", "title"],
                ),
                create_pr_tool,
                "high",
                S,
                writes=True,
                summarize=lambda a: f"PR in {a['repo']}: {a['head']} → {a.get('base', 'main')}: {a['title']}",
            ),
            ToolSpec(
                "github_list_issues",
                "Issues eines Repos.",
                _obj({**repo_prop, "state": {"type": "string"}}, ["repo"]),
                issues,
                "read",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "github_create_issue",
                "Legt ein Issue an.",
                _obj({**repo_prop, "title": {"type": "string"}, "body": {"type": "string"}}, ["repo", "title"]),
                create_issue,
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Issue in {a['repo']}: {a['title']}",
            ),
        ]
