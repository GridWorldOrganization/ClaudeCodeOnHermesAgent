"""Backlog MCP server (stdio transport).

Exposes Backlog API as MCP tools. Loaded by Hermes Agent via
~/.hermes/config.yaml mcp_servers entry. Token read from env
BACKLOG_API_KEY (injected by entrypoint.sh from Secrets Manager).
Domain/project defaults from env BACKLOG_DOMAIN / BACKLOG_DEFAULT_PROJECT_KEY.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("BACKLOG_API_KEY", "")
DOMAIN = os.environ.get("BACKLOG_DOMAIN", "")  # e.g. "yourspace.backlog.com"
DEFAULT_PROJECT = os.environ.get("BACKLOG_DEFAULT_PROJECT_KEY", "")

mcp = FastMCP("backlog")


def _request(method: str, path: str, params: dict[str, Any] | None = None,
             data: dict[str, Any] | None = None) -> Any:
    if not API_KEY:
        raise RuntimeError("BACKLOG_API_KEY env var not set")
    qs_params = dict(params or {})
    qs_params["apiKey"] = API_KEY
    url = f"https://{DOMAIN}/api/v2{path}?{urllib.parse.urlencode(qs_params, doseq=True)}"
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/x-www-form-urlencoded"} if body else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
        return json.loads(text) if text else None


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """List Backlog projects accessible to the API key.

    Returns:
        List of {id, projectKey, name, archived, ...} dicts.
    """
    projects = _request("GET", "/projects") or []
    return [
        {
            "id": p.get("id"),
            "projectKey": p.get("projectKey"),
            "name": p.get("name"),
            "archived": p.get("archived"),
        }
        for p in projects
    ]


@mcp.tool()
def list_issues(project_key: str = "", count: int = 20,
                status: str = "", keyword: str = "") -> list[dict[str, Any]]:
    """List Backlog issues with optional filters.

    Args:
        project_key: Project key (e.g., "GJ_AI"). Defaults to BACKLOG_DEFAULT_PROJECT_KEY env.
        count: Max issues to return (1-100, default 20)
        status: Filter by status name (e.g., "未対応", "処理中")
        keyword: Free-text keyword search

    Returns:
        List of issue summary dicts.
    """
    pk = project_key or DEFAULT_PROJECT
    if not pk:
        raise RuntimeError("project_key required (or set BACKLOG_DEFAULT_PROJECT_KEY)")

    # Resolve projectKey → projectId
    proj = _request("GET", f"/projects/{pk}")
    project_id = proj.get("id")

    params: dict[str, Any] = {
        "projectId[]": [project_id],
        "count": min(count, 100),
        "sort": "updated",
        "order": "desc",
    }
    if keyword:
        params["keyword"] = keyword

    issues = _request("GET", "/issues", params=params) or []

    # Optional client-side status filter (Backlog API status filter requires statusId)
    if status:
        issues = [i for i in issues if i.get("status", {}).get("name") == status]

    return [
        {
            "id": i.get("id"),
            "issueKey": i.get("issueKey"),
            "summary": i.get("summary"),
            "status": i.get("status", {}).get("name"),
            "assignee": (i.get("assignee") or {}).get("name"),
            "updated": i.get("updated"),
            "url": f"https://{DOMAIN}/view/{i.get('issueKey')}",
        }
        for i in issues
    ]


@mcp.tool()
def get_issue(issue_key: str) -> dict[str, Any]:
    """Get full detail of a single Backlog issue.

    Args:
        issue_key: Issue key like "GJ_AI-1"

    Returns:
        Full issue dict including description, comments count, etc.
    """
    issue = _request("GET", f"/issues/{issue_key}")
    if not issue:
        return {}
    return {
        "id": issue.get("id"),
        "issueKey": issue.get("issueKey"),
        "summary": issue.get("summary"),
        "description": issue.get("description"),
        "status": issue.get("status", {}).get("name"),
        "priority": issue.get("priority", {}).get("name"),
        "assignee": (issue.get("assignee") or {}).get("name"),
        "createdUser": (issue.get("createdUser") or {}).get("name"),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "dueDate": issue.get("dueDate"),
        "url": f"https://{DOMAIN}/view/{issue.get('issueKey')}",
    }


@mcp.tool()
def get_issue_comments(issue_key: str, count: int = 20,
                        order: str = "desc") -> list[dict[str, Any]]:
    """Get comments on a Backlog issue.

    Args:
        issue_key: Issue key like "GJ_AI-1"
        count: Max comments (1-100, default 20)
        order: "asc" or "desc" (default "desc")

    Returns:
        List of comment dicts with {id, content, createdUser, created}.
    """
    params = {"count": min(count, 100), "order": order}
    comments = _request("GET", f"/issues/{issue_key}/comments", params=params) or []
    return [
        {
            "id": c.get("id"),
            "content": c.get("content"),
            "createdUser": (c.get("createdUser") or {}).get("name"),
            "created": c.get("created"),
        }
        for c in comments
    ]


@mcp.tool()
def add_issue_comment(issue_key: str, content: str) -> dict[str, Any]:
    """Add a comment to a Backlog issue.

    Args:
        issue_key: Issue key like "GJ_AI-1"
        content: Comment text (Backlog notation supported: ## heading, * bullet, etc.)

    Returns:
        Created comment dict.
    """
    return _request("POST", f"/issues/{issue_key}/comments", data={"content": content})


@mcp.tool()
def search_issues(keyword: str, count: int = 20) -> list[dict[str, Any]]:
    """Search Backlog issues by free-text keyword across all accessible projects.

    Args:
        keyword: Search term (matches summary, description, comments)
        count: Max results (1-100, default 20)

    Returns:
        List of matching issue summaries.
    """
    params = {"keyword": keyword, "count": min(count, 100), "sort": "updated", "order": "desc"}
    issues = _request("GET", "/issues", params=params) or []
    return [
        {
            "issueKey": i.get("issueKey"),
            "summary": i.get("summary"),
            "status": i.get("status", {}).get("name"),
            "url": f"https://{DOMAIN}/view/{i.get('issueKey')}",
        }
        for i in issues
    ]


if __name__ == "__main__":
    mcp.run()
