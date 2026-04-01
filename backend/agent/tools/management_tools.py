# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Management tools service class — repo, git, and graph management via internal HTTP API.

Shared by both MCP and native (LangChain) tool layers.
"""

import asyncio
import json

import httpx
import requests
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from .tool_registry import TOOL_DESCRIPTIONS


class ManagementTools:
    """Repository/git/graph management tools via internal HTTP API calls.

    Provides both sync helpers (``_get`` / ``_post`` / ``_delete``, used by
    LangChain tools) and async helpers (``_aget`` / ``_apost`` / ``_adelete``,
    used by MCP tools) so the caller can pick the right variant.
    """

    def __init__(self, api_port: int | None = None):
        from core.config import settings

        self.api_port = api_port or settings.API_PORT
        self.base_url = f"http://localhost:{self.api_port}"

    def _resolve_repo_path(self, project_name: str) -> str | None:
        """Resolve repo path from project name by querying repos list or checking default location."""
        info = self._get("/api/repos/list")
        repos = info.get("repos", info.get("repositories", []))
        for r in repos:
            name = r.get("name", r.get("project_name", ""))
            if name == project_name:
                return r.get("path", r.get("local_path"))
        # Fallback: try wiki_repos default location
        from core.config import get_wiki_repos_dir

        default = get_wiki_repos_dir() / project_name
        if default.exists():
            return str(default)
        return None

    def _ensure_sync_manager(self, project_name: str) -> str | None:
        """Ensure sync manager is initialized for project. Returns repo_path used, or None on failure."""
        repo_path = self._resolve_repo_path(project_name)
        if not repo_path:
            return None
        status = self._get(f"/api/sync/{project_name}/status")
        if "error" not in status:
            return repo_path
        params = (
            f"?repo_path={repo_path}"
            f"&skip_embeddings=true"
            f"&auto_watch=false"
            f"&initial_sync=false"
        )
        self._post(f"/api/sync/{project_name}/start{params}", timeout=15)
        return repo_path

    # ── Sync HTTP helpers (for LangChain / thread-pool callers) ──────────

    def _post(self, path: str, json_data: dict | None = None, timeout: int = 30) -> dict:
        """POST to internal API."""
        try:
            resp = requests.post(f"{self.base_url}{path}", json=json_data, timeout=timeout)
            if resp.status_code in (200, 201):
                return resp.json()
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return {"error": f"API error ({resp.status_code}): {detail}"}
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    def _get(self, path: str, timeout: int = 10) -> dict:
        """GET from internal API."""
        try:
            resp = requests.get(f"{self.base_url}{path}", timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    def _delete(self, path: str, timeout: int = 30) -> dict:
        """DELETE from internal API."""
        try:
            resp = requests.delete(f"{self.base_url}{path}", timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    # ── Async HTTP helpers (for MCP tools running on the event loop) ─────

    async def _aget(self, path: str, timeout: int = 10) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except httpx.ConnectError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    async def _apost(self, path: str, json_data: dict | None = None, timeout: int = 30) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                resp = await client.post(path, json=json_data)
                if resp.status_code in (200, 201):
                    return resp.json()
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                return {"error": f"API error ({resp.status_code}): {detail}"}
        except httpx.ConnectError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    async def _adelete(self, path: str, timeout: int = 30) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                resp = await client.delete(path)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except httpx.ConnectError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    async def _aresolve_repo_path(self, project_name: str) -> str | None:
        """Async version of _resolve_repo_path."""
        info = await self._aget("/api/repos/list")
        repos = info.get("repos", info.get("repositories", []))
        for r in repos:
            name = r.get("name", r.get("project_name", ""))
            if name == project_name:
                return r.get("path", r.get("local_path"))
        from core.config import get_wiki_repos_dir
        default = get_wiki_repos_dir() / project_name
        if default.exists():
            return str(default)
        return None

    async def _aensure_sync_manager(self, project_name: str) -> str | None:
        """Async version of _ensure_sync_manager."""
        repo_path = await self._aresolve_repo_path(project_name)
        if not repo_path:
            return None
        status = await self._aget(f"/api/sync/{project_name}/status")
        if "error" not in status:
            return repo_path
        params = (
            f"?repo_path={repo_path}"
            f"&skip_embeddings=true"
            f"&auto_watch=false"
            f"&initial_sync=false"
        )
        await self._apost(f"/api/sync/{project_name}/start{params}", timeout=15)
        return repo_path

    # -------------------------------------------------------------------------
    # Repository management
    # -------------------------------------------------------------------------

    def list_repos(self) -> str:
        """List all indexed repositories."""
        result = self._get("/api/repos/list")
        return json.dumps(result, default=str)

    def add_repo(
        self,
        repo_url: str | None = None,
        local_path: str | None = None,
        project_name: str | None = None,
        skip_embeddings: bool = True,
    ) -> str:
        """Add a repository (remote URL or local path) and build its graph."""
        if local_path:
            data = {
                "local_path": local_path,
                "project_name": project_name,
                "subdirs": [],
                "skip_embeddings": skip_embeddings,
            }
            result = self._post("/api/repos/add-multiple-local", data, timeout=30)
        elif repo_url:
            data = {
                "repo_url": repo_url,
                "project_name": project_name,
                "skip_embeddings": skip_embeddings,
            }
            result = self._post("/api/repos/add", data, timeout=60)
        else:
            result = {"error": "Provide either repo_url or local_path."}

        # Treat 409 (already exists) as success — the repo is ready to use
        if isinstance(result, dict) and "error" in result:
            err_msg = result["error"]
            if "409" in str(err_msg) and "already exists" in str(err_msg).lower():
                name = project_name or (repo_url.rstrip("/").split("/")[-1].replace(".git", "") if repo_url else local_path)
                result = {"status": "already_exists", "message": f"Repository '{name}' already exists and is ready to use. You can query it directly with find_nodes, explore_code, etc."}

        return json.dumps(result, default=str)

    def remove_repo(self, repo_name: str) -> str:
        """Remove a repository and its data."""
        result = self._delete(f"/api/repos/{repo_name}?delete_graph=true&delete_docs=true")
        return json.dumps(result, default=str)

    def build_graph(
        self,
        project_path: str,
        project_name: str | None = None,
        fast_mode: bool = True,
    ) -> str:
        """Build knowledge graph (delegates to add_repo with local_path)."""
        return self.add_repo(local_path=project_path, project_name=project_name, skip_embeddings=fast_mode)

    def refresh_graph(self, project_name: str, fast_mode: bool = True) -> str:
        """Refresh an existing graph by re-building it."""
        # Look up project path first
        info = self._get(f"/api/repos/list")
        if "error" in info:
            return json.dumps(info)

        repos = info.get("repos", info.get("repositories", []))
        path = None
        for r in repos:
            name = r.get("name", r.get("project_name", ""))
            if name == project_name or name == f"{project_name}_claude":
                path = r.get("path", r.get("local_path"))
                project_name = name
                break

        if not path:
            return json.dumps({"error": f"Project '{project_name}' not found."})

        return self.add_repo(local_path=path, project_name=project_name, skip_embeddings=fast_mode)

    def get_task_status(self, task_id: str) -> str:
        """Check status of a background task (build, refresh, etc.)."""
        result = self._get(f"/api/tasks/{task_id}")
        return json.dumps(result, default=str)

    # -------------------------------------------------------------------------
    # Sync operations
    # -------------------------------------------------------------------------

    def start_sync(
        self,
        project_name: str,
        repo_path: str,
        skip_embeddings: bool = False,
        track_variables: bool = True,
        auto_watch: bool = True,
        subdirs: str | None = None,
        initial_sync: bool = True,
        use_polling: bool = False,
    ) -> str:
        """Start real-time file monitoring to keep graph updated."""
        params = (
            f"?repo_path={repo_path}"
            f"&skip_embeddings={str(skip_embeddings).lower()}"
            f"&track_variables={str(track_variables).lower()}"
            f"&auto_watch={str(auto_watch).lower()}"
            f"&initial_sync={str(initial_sync).lower()}"
            f"&use_polling={str(use_polling).lower()}"
        )
        if subdirs:
            params += f"&subdirs={subdirs}"
        try:
            resp = requests.post(
                f"{self.base_url}/api/sync/{project_name}/start{params}", timeout=30
            )
            return json.dumps(resp.json(), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def stop_sync(self, project_name: str) -> str:
        """Stop real-time file monitoring for a project."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/sync/{project_name}/stop", timeout=30
            )
            return resp.text
        except Exception as e:
            return json.dumps({"error": str(e)})

    def sync_now(
        self, project_name: str, repo_path: str | None = None, skip_embeddings: bool = False
    ) -> str:
        """One-shot incremental sync: detect file changes and update graph."""
        # Auto-resolve repo_path if not provided
        if not repo_path:
            repo_path = self._resolve_repo_path(project_name)
        url = (
            f"{self.base_url}/api/sync/{project_name}/now"
            f"?skip_embeddings={str(skip_embeddings).lower()}"
        )
        if repo_path:
            url += f"&repo_path={repo_path}"
        try:
            resp = requests.post(url, timeout=120)
            return json.dumps(resp.json(), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_sync_status(self, project_name: str) -> str:
        """Check if file monitoring is active and see pending changes."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/sync/{project_name}/status", timeout=10
            )
            if resp.status_code == 404:
                return json.dumps({
                    "status": "not_initialized",
                    "message": f"Sync manager for '{project_name}' is not initialized. Use sync(action='start') or git(action='pull') to initialize.",
                })
            return resp.text
        except Exception as e:
            return json.dumps({"error": str(e)})

    # -------------------------------------------------------------------------
    # Git operations
    # -------------------------------------------------------------------------

    def git_checkout(self, project_name: str, ref: str) -> str:
        """Checkout a git ref for a repository."""
        result = self._post(f"/api/sync/{project_name}/git/checkout", {"ref": ref}, timeout=60)
        # If sync manager not found, auto-resolve repo_path and retry
        if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
            repo_path = self._resolve_repo_path(project_name)
            if repo_path:
                result = self._post(
                    f"/api/sync/{project_name}/git/checkout?repo_path={repo_path}",
                    {"ref": ref}, timeout=60,
                )
            else:
                result = {"error": f"Project '{project_name}' not found. Use list_repos() to see available projects."}
        return json.dumps(result, default=str)

    def git_fetch(self, project_name: str, remote: str = "origin") -> str:
        """Fetch latest changes from remote."""
        result = self._post(f"/api/sync/{project_name}/git/fetch", {"remote": remote})
        # If sync manager not found, initialize it and retry
        if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
            if self._ensure_sync_manager(project_name):
                result = self._post(f"/api/sync/{project_name}/git/fetch", {"remote": remote})
            else:
                result = {"error": f"Project '{project_name}' not found. Use list_repos() to see available projects."}
        return json.dumps(result, default=str)

    def git_pull(self, project_name: str, remote: str = "origin", branch: str | None = None) -> str:
        """Pull updates from remote (fetch + merge) and update knowledge graph."""
        body: dict = {"remote": remote}
        if branch:
            body["branch"] = branch
        # Try without repo_path first
        result = self._post(f"/api/sync/{project_name}/git/pull", body, timeout=120)
        # If sync manager not found, auto-resolve repo_path and retry
        if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
            repo_path = self._resolve_repo_path(project_name)
            if repo_path:
                result = self._post(
                    f"/api/sync/{project_name}/git/pull?repo_path={repo_path}",
                    body, timeout=120,
                )
            else:
                result = {"error": f"Project '{project_name}' not found. Use list_repos() to see available projects."}
        return json.dumps(result, default=str)

    def git_list_refs(self, project_name: str) -> str:
        """List branches and tags."""
        branches = self._get(f"/api/sync/{project_name}/git/branches")
        # If sync manager not found, initialize it and retry all calls
        if isinstance(branches, dict) and "error" in branches and "not found" in str(branches["error"]).lower():
            if self._ensure_sync_manager(project_name):
                branches = self._get(f"/api/sync/{project_name}/git/branches")
            else:
                return json.dumps({"error": f"Project '{project_name}' not found. Use list_repos() to see available projects."})
        tags = self._get(f"/api/sync/{project_name}/git/tags")
        current = self._get(f"/api/sync/{project_name}/git/current")
        return json.dumps(
            {"branches": branches, "tags": tags, "current": current},
            default=str,
        )

    # -------------------------------------------------------------------------
    # Compound tools (shared by MCP and native)
    # -------------------------------------------------------------------------

    def git(
        self,
        project_name: str,
        action: str,
        ref: str | None = None,
        remote: str = "origin",
        branch: str | None = None,
    ) -> str:
        """Git operations: checkout, fetch, list_refs, pull."""
        if action == "checkout":
            if not ref:
                return json.dumps({"error": "ref is required for checkout action"})
            return self.git_checkout(project_name, ref)
        elif action == "fetch":
            return self.git_fetch(project_name, remote)
        elif action == "list_refs":
            return self.git_list_refs(project_name)
        elif action == "pull":
            return self.git_pull(project_name, remote, branch)
        return json.dumps({"error": f"Unknown git action: {action}. Use: checkout, fetch, list_refs, pull"})

    def sync(
        self,
        project_name: str,
        action: str,
        repo_path: str | None = None,
        subdirs: str | None = None,
    ) -> str:
        """Sync operations: start, stop, now, status."""
        if action == "start":
            # Auto-resolve repo_path if not provided
            if not repo_path:
                repo_path = self._resolve_repo_path(project_name)
            if not repo_path:
                return json.dumps({"error": f"repo_path is required for start action and could not be auto-resolved for '{project_name}'. Use list_repos() to see available projects."})
            return self.start_sync(project_name, repo_path, subdirs=subdirs)
        elif action == "stop":
            return self.stop_sync(project_name)
        elif action == "now":
            return self.sync_now(project_name, repo_path=repo_path)
        elif action == "status":
            return self.get_sync_status(project_name)
        return json.dumps({"error": f"Unknown sync action: {action}. Use: start, stop, now, status"})

    def manage_repo(
        self,
        action: str,
        repo_url: str | None = None,
        local_path: str | None = None,
        project_name: str | None = None,
        repo_name: str | None = None,
    ) -> str:
        """Repository lifecycle: add, remove, clean."""
        if action == "add":
            return self.add_repo(repo_url=repo_url, local_path=local_path, project_name=project_name)
        elif action == "remove":
            if not repo_name:
                return json.dumps({"error": "repo_name is required for remove action"})
            return self.remove_repo(repo_name)
        elif action == "clean":
            if not repo_name:
                return json.dumps({"error": "repo_name is required for clean action"})
            # clean_graph delegates to ingestor; reuse remove_repo path but only delete graph
            result = self._delete(f"/api/repos/{repo_name}?delete_graph=true&delete_docs=false")
            return json.dumps(result, default=str)
        return json.dumps({"error": f"Unknown manage_repo action: {action}. Use: add, remove, clean"})

    def manage_graph(
        self,
        action: str,
        project_path: str | None = None,
        project_name: str | None = None,
        fast_mode: bool = True,
        job_id: str | None = None,
    ) -> str:
        """Knowledge graph build, refresh, and job status."""
        if action == "build":
            if not project_path:
                return json.dumps({"error": "project_path is required for build action"})
            return self.build_graph(project_path, project_name=project_name, fast_mode=fast_mode)
        elif action == "refresh":
            if not project_name:
                return json.dumps({"error": "project_name is required for refresh action"})
            return self.refresh_graph(project_name, fast_mode=fast_mode)
        elif action == "job_status":
            if not job_id:
                return json.dumps({"error": "job_id is required for job_status action"})
            return self.get_task_status(job_id)
        return json.dumps({"error": f"Unknown manage_graph action: {action}. Use: build, refresh, job_status"})

    # ── Async versions of compound methods (for MCP) ─────────────────────

    async def alist_repos(self) -> str:
        result = await self._aget("/api/repos/list")
        return json.dumps(result, default=str)

    async def aadd_repo(
        self, repo_url: str | None = None, local_path: str | None = None,
        project_name: str | None = None, skip_embeddings: bool = True,
    ) -> str:
        if local_path:
            data = {"local_path": local_path, "project_name": project_name,
                    "subdirs": [], "skip_embeddings": skip_embeddings}
            result = await self._apost("/api/repos/add-multiple-local", data, timeout=30)
        elif repo_url:
            data = {"repo_url": repo_url, "project_name": project_name,
                    "skip_embeddings": skip_embeddings}
            result = await self._apost("/api/repos/add", data, timeout=300)
        else:
            result = {"error": "Provide either repo_url or local_path."}

        if isinstance(result, dict) and "error" in result:
            err_msg = result["error"]
            if "409" in str(err_msg) and "already exists" in str(err_msg).lower():
                name = project_name or (repo_url.rstrip("/").split("/")[-1].replace(".git", "") if repo_url else local_path)
                result = {"status": "already_exists", "message": f"Repository '{name}' already exists and is ready to use."}
        return json.dumps(result, default=str)

    async def aremove_repo(self, repo_name: str) -> str:
        result = await self._adelete(f"/api/repos/{repo_name}?delete_graph=true&delete_docs=true")
        return json.dumps(result, default=str)

    async def abuild_graph(self, project_path: str, project_name: str | None = None, fast_mode: bool = True) -> str:
        return await self.aadd_repo(local_path=project_path, project_name=project_name, skip_embeddings=fast_mode)

    async def arefresh_graph(self, project_name: str, fast_mode: bool = True) -> str:
        info = await self._aget("/api/repos/list")
        if "error" in info:
            return json.dumps(info)
        repos = info.get("repos", info.get("repositories", []))
        path = None
        for r in repos:
            name = r.get("name", r.get("project_name", ""))
            if name == project_name or name == f"{project_name}_claude":
                path = r.get("path", r.get("local_path"))
                project_name = name
                break
        if not path:
            return json.dumps({"error": f"Project '{project_name}' not found."})
        return await self.aadd_repo(local_path=path, project_name=project_name, skip_embeddings=fast_mode)

    async def aget_task_status(self, task_id: str) -> str:
        result = await self._aget(f"/api/tasks/{task_id}")
        return json.dumps(result, default=str)

    async def amanage_repo(self, action: str, repo_url: str | None = None,
                           local_path: str | None = None, project_name: str | None = None,
                           repo_name: str | None = None) -> str:
        if action == "add":
            return await self.aadd_repo(repo_url=repo_url, local_path=local_path, project_name=project_name)
        elif action == "remove":
            if not repo_name:
                return json.dumps({"error": "repo_name is required for remove action"})
            return await self.aremove_repo(repo_name)
        elif action == "clean":
            if not repo_name:
                return json.dumps({"error": "repo_name is required for clean action"})
            result = await self._adelete(f"/api/repos/{repo_name}?delete_graph=true&delete_docs=false")
            return json.dumps(result, default=str)
        return json.dumps({"error": f"Unknown manage_repo action: {action}. Use: add, remove, clean"})

    async def amanage_graph(self, action: str, project_path: str | None = None,
                            project_name: str | None = None, fast_mode: bool = True,
                            job_id: str | None = None) -> str:
        if action == "build":
            if not project_path:
                return json.dumps({"error": "project_path is required for build action"})
            return await self.abuild_graph(project_path, project_name=project_name, fast_mode=fast_mode)
        elif action == "refresh":
            if not project_name:
                return json.dumps({"error": "project_name is required for refresh action"})
            return await self.arefresh_graph(project_name, fast_mode=fast_mode)
        elif action == "job_status":
            if not job_id:
                return json.dumps({"error": "job_id is required for job_status action"})
            return await self.aget_task_status(job_id)
        return json.dumps({"error": f"Unknown manage_graph action: {action}. Use: build, refresh, job_status"})

    async def agit(self, project_name: str, action: str, ref: str | None = None,
                   remote: str = "origin", branch: str | None = None) -> str:
        if action == "checkout":
            if not ref:
                return json.dumps({"error": "ref is required for checkout action"})
            result = await self._apost(f"/api/sync/{project_name}/git/checkout", {"ref": ref}, timeout=60)
            if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
                repo_path = await self._aresolve_repo_path(project_name)
                if repo_path:
                    result = await self._apost(f"/api/sync/{project_name}/git/checkout?repo_path={repo_path}", {"ref": ref}, timeout=60)
                else:
                    result = {"error": f"Project '{project_name}' not found."}
            return json.dumps(result, default=str)
        elif action == "fetch":
            result = await self._apost(f"/api/sync/{project_name}/git/fetch", {"remote": remote})
            if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
                if await self._aensure_sync_manager(project_name):
                    result = await self._apost(f"/api/sync/{project_name}/git/fetch", {"remote": remote})
                else:
                    result = {"error": f"Project '{project_name}' not found."}
            return json.dumps(result, default=str)
        elif action == "list_refs":
            branches = await self._aget(f"/api/sync/{project_name}/git/branches")
            if isinstance(branches, dict) and "error" in branches and "not found" in str(branches["error"]).lower():
                if await self._aensure_sync_manager(project_name):
                    branches = await self._aget(f"/api/sync/{project_name}/git/branches")
                else:
                    return json.dumps({"error": f"Project '{project_name}' not found."})
            tags = await self._aget(f"/api/sync/{project_name}/git/tags")
            current = await self._aget(f"/api/sync/{project_name}/git/current")
            return json.dumps({"branches": branches, "tags": tags, "current": current}, default=str)
        elif action == "pull":
            body: dict = {"remote": remote}
            if branch:
                body["branch"] = branch
            result = await self._apost(f"/api/sync/{project_name}/git/pull", body, timeout=120)
            if isinstance(result, dict) and "error" in result and "not found" in str(result["error"]).lower():
                repo_path = await self._aresolve_repo_path(project_name)
                if repo_path:
                    result = await self._apost(f"/api/sync/{project_name}/git/pull?repo_path={repo_path}", body, timeout=120)
                else:
                    result = {"error": f"Project '{project_name}' not found."}
            return json.dumps(result, default=str)
        return json.dumps({"error": f"Unknown git action: {action}. Use: checkout, fetch, list_refs, pull"})

    async def async_sync(self, project_name: str, action: str, repo_path: str | None = None,
                         subdirs: str | None = None) -> str:
        if action == "start":
            if not repo_path:
                repo_path = await self._aresolve_repo_path(project_name)
            if not repo_path:
                return json.dumps({"error": f"repo_path is required for start action and could not be auto-resolved for '{project_name}'."})
            params = (
                f"?repo_path={repo_path}&skip_embeddings=false&track_variables=true"
                f"&auto_watch=true&initial_sync=true&use_polling=false"
            )
            if subdirs:
                params += f"&subdirs={subdirs}"
            result = await self._apost(f"/api/sync/{project_name}/start{params}", timeout=30)
            return json.dumps(result, default=str)
        elif action == "stop":
            result = await self._apost(f"/api/sync/{project_name}/stop", timeout=30)
            return json.dumps(result, default=str)
        elif action == "now":
            url = f"/api/sync/{project_name}/now?skip_embeddings=false"
            if repo_path:
                url += f"&repo_path={repo_path}"
            elif not repo_path:
                resolved = await self._aresolve_repo_path(project_name)
                if resolved:
                    url += f"&repo_path={resolved}"
            result = await self._apost(url, timeout=120)
            return json.dumps(result, default=str)
        elif action == "status":
            result = await self._aget(f"/api/sync/{project_name}/status")
            if "error" in result and ("404" in str(result["error"]) or "not found" in str(result["error"]).lower()):
                return json.dumps({"status": "not_initialized", "message": f"Sync manager for '{project_name}' is not initialized."})
            return json.dumps(result, default=str)
        return json.dumps({"error": f"Unknown sync action: {action}. Use: start, stop, now, status"})


# =============================================================================
# LangChain tool wrappers for native path (compound tools)
# =============================================================================


class ListReposInput(BaseModel):
    """No arguments needed."""
    pass


class GitInput(BaseModel):
    project_name: str = Field(description="Repository name")
    action: str = Field(description='"checkout" | "fetch" | "list_refs" | "pull"')
    ref: str | None = Field(default=None, description="Branch/tag/commit (for checkout)")
    remote: str = Field(default="origin", description="Remote name (for fetch/pull)")
    branch: str | None = Field(default=None, description="Branch to pull (for pull)")


class SyncInput(BaseModel):
    project_name: str = Field(description="Repository name")
    action: str = Field(description='"start" | "stop" | "now" | "status"')
    repo_path: str | None = Field(default=None, description='Repository path (required for "start")')
    subdirs: str | None = Field(default=None, description='Comma-separated subdirectories to watch (for "start")')


class ManageRepoInput(BaseModel):
    action: str = Field(description='"add" | "remove" | "clean"')
    repo_url: str | None = Field(default=None, description="Git remote URL (for add)")
    local_path: str | None = Field(default=None, description="Local filesystem path (for add)")
    project_name: str | None = Field(default=None, description="Custom project name (for add)")
    repo_name: str | None = Field(default=None, description="Repo name from list_repos() (for remove/clean)")


class ManageGraphInput(BaseModel):
    action: str = Field(description='"build" | "refresh" | "job_status"')
    project_path: str | None = Field(default=None, description="Absolute path to project root (for build)")
    project_name: str | None = Field(default=None, description="Project name (for build/refresh)")
    fast_mode: bool = Field(default=True, description="Skip embeddings (for build/refresh)")
    job_id: str | None = Field(default=None, description="Job ID (for job_status)")


def create_management_tools(api_port: int | None = None) -> list[BaseTool]:
    """Create LangChain management tools for native agent path (compound tools)."""
    mgmt = ManagementTools(api_port=api_port)
    tools: list[BaseTool] = []

    tools.append(
        StructuredTool.from_function(
            func=mgmt.list_repos,
            name="list_repos",
            description=TOOL_DESCRIPTIONS["list_repos"],
            args_schema=ListReposInput,
        )
    )
    tools.append(
        StructuredTool.from_function(
            func=mgmt.git,
            name="git",
            description=TOOL_DESCRIPTIONS["git"],
            args_schema=GitInput,
        )
    )
    tools.append(
        StructuredTool.from_function(
            func=mgmt.sync,
            name="sync",
            description=TOOL_DESCRIPTIONS["sync"],
            args_schema=SyncInput,
        )
    )
    tools.append(
        StructuredTool.from_function(
            func=mgmt.manage_repo,
            name="manage_repo",
            description=TOOL_DESCRIPTIONS["manage_repo"],
            args_schema=ManageRepoInput,
        )
    )
    tools.append(
        StructuredTool.from_function(
            func=mgmt.manage_graph,
            name="manage_graph",
            description=TOOL_DESCRIPTIONS["manage_graph"],
            args_schema=ManageGraphInput,
        )
    )

    return tools
