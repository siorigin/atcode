# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import time
from pathlib import Path

from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from core.config import settings
from core.git_executable import GIT
from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# ============== Request/Response Models ==============


class AddRepoRequest(BaseModel):
    """Request to add a new repository."""

    repo_url: str = Field(..., description="Git repository URL")
    branch: str | None = Field(None, description="Specific branch to clone")
    username: str | None = Field(None, description="Git username for private repos")
    password: str | None = Field(
        None, description="Git password/token for private repos"
    )
    skip_embeddings: bool | None = Field(
        True,
        description="Skip semantic embeddings generation (default: True for faster builds)",
    )


class AddLocalRepoRequest(BaseModel):
    """Request to add a local repository (without cloning)."""

    local_path: str = Field(..., description="Absolute path to the local repository")
    project_name: str | None = Field(
        None, description="Custom project name (defaults to directory name)"
    )
    skip_embeddings: bool | None = Field(
        True,
        description="Skip semantic embeddings generation (default: True for faster builds)",
    )


# Git clone timeout in seconds (5 minutes default)
GIT_CLONE_TIMEOUT = int(os.environ.get("GIT_CLONE_TIMEOUT", "300"))


class AddRepoResponse(BaseModel):
    """Response for add repository request."""

    success: bool = Field(..., description="Whether the request was accepted")
    task_id: str = Field(..., description="Task ID for tracking progress")
    repo_name: str = Field(..., description="Repository name extracted from URL")
    message: str = Field("", description="Status message")


class RepoInfo(BaseModel):
    """Information about a repository."""

    name: str = Field(..., description="Repository name")
    has_graph: bool = Field(False, description="Whether knowledge graph exists")
    has_docs: bool = Field(False, description="Whether documentation exists")
    research_count: int = Field(0, description="Number of research documents")
    path: str = Field("", description="Local path to repository")


class RepoListResponse(BaseModel):
    """Response for listing repositories."""

    repos: list[RepoInfo] = Field(
        default_factory=list, description="List of repositories"
    )
    total: int = Field(0, description="Total number of repositories")


class SubdirectoryInfo(BaseModel):
    """Information about a subdirectory."""

    name: str = Field(..., description="Directory name")
    path: str = Field(..., description="Full path to directory")
    file_count: int = Field(0, description="Number of files in directory (approximate)")
    has_python: bool = Field(
        False, description="Whether directory contains Python files"
    )
    has_init: bool = Field(
        False, description="Whether directory has __init__.py (is a package)"
    )


class ListSubdirectoriesResponse(BaseModel):
    """Response for listing subdirectories."""

    base_path: str = Field(..., description="The base path that was scanned")
    subdirectories: list[SubdirectoryInfo] = Field(
        default_factory=list, description="List of subdirectories"
    )
    total: int = Field(0, description="Total number of subdirectories")


class AddLocalRepoWithSubdirsRequest(BaseModel):
    """Request to add a local repository with subdirectory filtering."""

    project_name: str = Field(..., description="Project name")
    local_path: str = Field(..., description="Base directory path")
    subdirs: list[str] = Field(
        default_factory=list,
        description="Subdirectory names to include (filters processing)",
    )
    skip_embeddings: bool | None = Field(
        True,
        description="Skip semantic embeddings generation (default: True for faster builds)",
    )


# ============== Helper Functions ==============


def get_wiki_repos_path() -> Path:
    """Get the wiki_repos directory path."""
    from core.config import get_wiki_repos_dir

    return get_wiki_repos_dir()


def get_wiki_doc_path() -> Path:
    """Get the wiki_doc directory path."""
    from core.config import get_wiki_doc_dir

    return get_wiki_doc_dir()


def extract_repo_name(repo_url: str) -> str:
    """Extract repository name from URL."""
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def build_authenticated_url(
    repo_url: str, username: str | None, password: str | None
) -> str:
    """
    Build a git URL with embedded credentials for authentication.

    Supports both HTTPS URLs (with embedded credentials) and SSH URLs (unchanged).
    """
    if not username or not password:
        return repo_url

    # Only modify HTTPS URLs
    if repo_url.startswith("https://"):
        # URL encode credentials to handle special characters
        from urllib.parse import quote

        encoded_username = quote(username, safe="")
        encoded_password = quote(password, safe="")

        # Insert credentials after https://
        # Format: https://username:password@host/path
        return repo_url.replace(
            "https://", f"https://{encoded_username}:{encoded_password}@", 1
        )

    elif repo_url.startswith("http://"):
        from urllib.parse import quote

        encoded_username = quote(username, safe="")
        encoded_password = quote(password, safe="")
        return repo_url.replace(
            "http://", f"http://{encoded_username}:{encoded_password}@", 1
        )

    # For SSH URLs or other protocols, return unchanged
    return repo_url


async def process_add_repo_task(
    task_id: str,
    repo_url: str,
    repo_name: str,
    local_repo_path: Path,
    branch: str | None = None,
    username: str | None = None,
    password: str | None = None,
    skip_embeddings: bool = False,
):
    """Background task to clone repository and build knowledge graph."""
    task_manager = get_task_manager()

    # Capture the event loop from the main thread BEFORE spawning worker threads
    # This is critical: worker threads don't have their own event loop
    main_loop = asyncio.get_running_loop()

    try:
        # Update status to running
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=5,
            step="initializing",
            status_message="Preparing to clone repository...",
        )

        # Step 1: Clone repository
        await task_manager.update_task(
            task_id,
            progress=10,
            step="cloning",
            status_message=f"Cloning {repo_url}...",
        )

        # Build authenticated URL if credentials provided
        auth_url = build_authenticated_url(repo_url, username, password)

        # Build git clone command
        clone_cmd = [GIT, "clone"]
        if branch and branch.strip():
            clone_cmd.extend(["-b", branch.strip()])
        clone_cmd.extend([auth_url, str(local_repo_path)])

        # Log command (without credentials for security)
        safe_cmd = clone_cmd.copy()
        safe_cmd[-2] = repo_url  # Replace auth URL with original for logging
        logger.info(f"Cloning repository: {' '.join(safe_cmd)}")

        # Run git clone with timeout
        # Set GIT_TERMINAL_PROMPT=0 to prevent git from prompting for credentials
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"  # Disable interactive prompts
        env["GIT_LFS_SKIP_SMUDGE"] = "1"  # Skip LFS file downloads (binary files not needed for code analysis)

        process = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=GIT_CLONE_TIMEOUT
            )
        except TimeoutError:
            # Kill the process if it times out
            process.kill()
            await process.wait()
            raise Exception(
                f"Git clone timed out after {GIT_CLONE_TIMEOUT} seconds. "
                "This usually indicates a slow or blocked network connection to the git host, "
                "or a very large repository. "
                "If this is a private repository, please provide username and password/access token."
            )

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Clone failed"
            # Check for common authentication errors
            if (
                "Authentication failed" in error_msg
                or "could not read Username" in error_msg
            ):
                raise Exception(
                    "Git authentication failed. For private repositories, "
                    "please provide your username and password/access token."
                )

            # Handle "Clone succeeded, but checkout failed" — recover the working tree
            if "Clone succeeded, but checkout failed" in error_msg:
                logger.warning(
                    "Git clone checkout failed, attempting recovery with git restore..."
                )
                await task_manager.update_task(
                    task_id,
                    progress=25,
                    step="recovering_checkout",
                    status_message="Clone checkout failed, recovering working tree...",
                )
                recover = await asyncio.create_subprocess_exec(
                    GIT, "restore", "--source=HEAD", ":/",
                    cwd=str(local_repo_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                r_stdout, r_stderr = await recover.communicate()
                if recover.returncode == 0:
                    logger.info("Successfully recovered working tree after clone")
                else:
                    # Fallback: try git checkout -f
                    recover2 = await asyncio.create_subprocess_exec(
                        GIT, "checkout", "-f", "HEAD",
                        cwd=str(local_repo_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                    await recover2.communicate()
                    if recover2.returncode != 0:
                        raise Exception(f"Git clone failed: {error_msg}")
                    logger.info("Recovered working tree via git checkout -f")
            else:
                raise Exception(f"Git clone failed: {error_msg}")

        await task_manager.update_task(
            task_id,
            progress=40,
            step="clone_complete",
            status_message="Repository cloned successfully. Building knowledge graph...",
        )

        # Step 2: Build knowledge graph using direct API call
        await task_manager.update_task(
            task_id,
            progress=50,
            step="building_graph",
            status_message="Building knowledge graph...",
        )

        # Import graph building components
        from concurrent.futures import ThreadPoolExecutor

        from core.config import settings
        from graph.service import MemgraphIngestor
        from graph.updater import GraphUpdater
        from parser.loader import load_parsers

        # Progress callback that updates the task status
        # Uses main_loop captured at function start (before spawning worker threads)
        def progress_callback(progress: int, message: str):
            """Callback to update task progress from GraphUpdater.

            Maps GraphUpdater progress (0-100) to overall task progress (50-95).
            """
            # Map progress: 0-100 from GraphUpdater to 50-95 in overall task
            mapped_progress = 50 + int(progress * 0.45)
            # Schedule the coroutine on the main event loop from worker thread
            try:
                asyncio.run_coroutine_threadsafe(
                    task_manager.update_task(
                        task_id,
                        progress=mapped_progress,
                        step="building_graph",
                        status_message=message,
                    ),
                    main_loop,
                )
            except Exception as e:
                logger.warning(f"Failed to update progress: {e}")

        logger.info(f"Building knowledge graph for: {local_repo_path}")

        try:
            # Create ingestor for write operations using unified config
            write_host, write_port = settings.get_write_connection()
            ingestor = MemgraphIngestor(
                host=write_host,
                port=write_port,
                batch_size=settings.MEMGRAPH_BATCH_SIZE,
            )

            with ingestor:
                # Clean existing data first
                ingestor.clean_project(repo_name)
                ingestor.ensure_constraints()

                # Load parsers with language objects for parallel parsing
                parsers, queries, language_objects = load_parsers(return_languages=True)

                # Create updater with progress callback
                updater = GraphUpdater(
                    ingestor,
                    local_repo_path,
                    parsers,
                    queries,
                    skip_embeddings=skip_embeddings,
                    progress_callback=progress_callback,
                    language_objects=language_objects,
                    enable_parallel_parsing=True,
                )

                # Run in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="graph_build"
                ) as executor:
                    await loop.run_in_executor(executor, updater.run)

            await task_manager.update_task(
                task_id,
                progress=95,
                step="graph_complete",
                status_message="Knowledge graph built successfully",
            )
        except Exception as graph_error:
            # Log but don't fail - graph building can be retried
            logger.warning(f"Graph build had issues: {graph_error}")
            await task_manager.update_task(
                task_id,
                progress=90,
                step="graph_warning",
                status_message=f"Graph build completed with warnings: {str(graph_error)[:100]}",
            )

        # Complete
        invalidate_repos_cache()
        # Keep graph overview endpoints in sync with the newly built project.
        # Without this, /api/graph/projects and /api/graph/stats can stay stuck
        # on the empty startup cache until Redis TTL expiry.
        try:
            from api.routes.graph import _update_project_in_cache

            _update_project_in_cache(repo_name)
        except Exception as cache_error:
            logger.debug(f"Failed to update graph cache for {repo_name}: {cache_error}")
        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            step="complete",
            status_message=f"Repository {repo_name} added successfully",
            result={
                "repo_name": repo_name,
                "repo_url": repo_url,
                "local_path": str(local_repo_path),
            },
        )

        logger.info(f"Successfully added repository: {repo_name}")

    except asyncio.CancelledError:
        logger.info(f"Add repo task cancelled: {task_id}")
        await task_manager.update_task(
            task_id,
            status=TaskStatus.CANCELLED,
            error="Task was cancelled by user",
        )
        # Clean up partial clone if exists
        if local_repo_path.exists():
            import shutil

            shutil.rmtree(local_repo_path, ignore_errors=True)
        raise

    except Exception as e:
        logger.error(f"Failed to add repository {repo_name}: {e}", exc_info=True)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
        )
        # Clean up partial clone if exists
        if local_repo_path.exists():
            import shutil

            shutil.rmtree(local_repo_path, ignore_errors=True)

    finally:
        # Always unregister the task when done
        task_manager.unregister_task(task_id)


# ============== API Endpoints ==============


@router.post(
    "/add",
    response_model=AddRepoResponse,
    summary="Add Repository",
    description="Clone a git repository and build its knowledge graph.",
)
async def add_repository(request: AddRepoRequest) -> AddRepoResponse:
    """
    Add a new repository to AtCode.

    This is a long-running operation that:
    1. Clones the repository from the given URL
    2. Builds a knowledge graph from the code

    In replication mode (REPLICA server), the request is proxied to the MAIN server
    so that the CPU-intensive build happens on MAIN, not REPLICA.

    Returns immediately with a task_id for tracking progress.
    """
    # In replication mode on REPLICA: proxy the request to MAIN server
    # This ensures the CPU-intensive build runs on MAIN, not here
    if settings.is_replica_node:
        logger.info(
            f"REPLICA mode: proxying add repo request to MAIN server {settings.MEMGRAPH_MAIN_HOST}"
        )
        return await _proxy_add_repo_to_main(request)

    # Local processing (MAIN server or standalone mode)
    try:
        repo_name = extract_repo_name(request.repo_url)
        wiki_repos_path = get_wiki_repos_path()
        local_repo_path = wiki_repos_path / repo_name

        # Check if repo already exists
        if local_repo_path.exists():
            raise HTTPException(
                status_code=409, detail=f"Repository already exists: {repo_name}"
            )

        # Ensure wiki_repos directory exists
        wiki_repos_path.mkdir(parents=True, exist_ok=True)

        # Create task
        task_manager = get_task_manager()
        task_id = await task_manager.create_task(
            task_type=TaskType.GRAPH_BUILD.value,
            repo_name=repo_name,
            initial_message=f"Adding repository {repo_name}...",
        )

        # Start background processing and register for cancellation support
        background_task = asyncio.create_task(
            process_add_repo_task(
                task_id=task_id,
                repo_url=request.repo_url,
                repo_name=repo_name,
                local_repo_path=local_repo_path,
                branch=request.branch,
                username=request.username,
                password=request.password,
                skip_embeddings=request.skip_embeddings or False,
            )
        )
        # Register the task so it can be cancelled
        task_manager.register_task(task_id, background_task)

        logger.info(f"Started add repo task: {task_id} for {repo_name}")

        return AddRepoResponse(
            success=True,
            task_id=task_id,
            repo_name=repo_name,
            message=f"Repository addition started. Track progress with task ID: {task_id}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start add repository: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def process_add_local_repo_task(
    task_id: str,
    project_name: str,
    local_path: Path,
    skip_embeddings: bool = False,
    subdirs: list[str] | None = None,
) -> None:
    """
    Background task to build knowledge graph from a local repository.

    This is similar to process_add_repo_task but skips the git clone step.

    Args:
        task_id: Task ID for progress tracking
        project_name: Name for the project
        local_path: Path to the local directory
        skip_embeddings: Whether to skip semantic embedding generation
        subdirs: Optional list of subdirectory names to include (filters processing)
    """
    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=5,
            status_message=f"Starting graph build for {project_name}...",
        )

        # Verify the path exists
        if not local_path.exists():
            raise FileNotFoundError(f"Local path does not exist: {local_path}")

        if not local_path.is_dir():
            raise ValueError(f"Path is not a directory: {local_path}")

        # Import here to avoid circular imports
        from core.config import settings
        from graph.service import MemgraphIngestor
        from graph.updater import GraphUpdater
        from parser.loader import load_parsers

        # Get write connection for database operations
        host, port = settings.get_write_connection()

        # Load parsers with language objects for parallel parsing
        parsers, queries, language_objects = load_parsers(return_languages=True)

        # Create progress callback
        main_loop = asyncio.get_event_loop()

        def progress_callback(progress: int, message: str):
            # Map progress from 0-100 to 10-95 range
            mapped_progress = 10 + int(progress * 0.85)
            asyncio.run_coroutine_threadsafe(
                task_manager.update_task(
                    task_id,
                    progress=mapped_progress,
                    status_message=message,
                ),
                main_loop,
            )

        # Build knowledge graph with proper connection handling
        def build_graph():
            # Use context manager for safe connection handling
            with MemgraphIngestor(host=host, port=port) as ingestor:
                # Clean existing data first (same as GitHub flow)
                ingestor.clean_project(project_name)
                ingestor.ensure_constraints()

                # Create updater with project_name and subdirs parameters
                updater = GraphUpdater(
                    ingestor,
                    local_path,
                    parsers,
                    queries,
                    skip_embeddings=skip_embeddings,
                    progress_callback=progress_callback,
                    language_objects=language_objects,
                    enable_parallel_parsing=True,
                    task_id=task_id,
                    project_name=project_name,  # Pass the user-specified project name
                    subdirs=subdirs,  # Pass subdirs filter to limit processing
                )

                # Run the graph build
                updater.run()

                # Return function count for result reporting
                return len(updater.function_registry)

        # Build knowledge graph
        await task_manager.update_task(
            task_id,
            progress=10,
            status_message="Building knowledge graph...",
        )

        # Run in thread pool to avoid blocking
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="local_graph_build"
        ) as executor:
            function_count = await loop.run_in_executor(executor, build_graph)

        # Yield to event loop so any pending progress_callback coroutines
        # (submitted via run_coroutine_threadsafe) complete before we set COMPLETED.
        # This prevents a race where a late progress update overwrites the COMPLETED state.
        # Use sleep(0.1) instead of sleep(0) to give multiple pending coroutines
        # enough event loop iterations to fully complete their store.save() calls.
        await asyncio.sleep(0.1)

        # Success
        invalidate_repos_cache()
        # Sync Redis L2 graph overview cache with the new project so the
        # repo list and graph panel reflect the build immediately.
        try:
            from api.routes.graph import _update_project_in_cache

            _update_project_in_cache(project_name)
        except Exception as cache_error:
            logger.debug(
                f"Failed to update graph cache for {project_name}: {cache_error}"
            )
        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            status_message=f"Knowledge graph built successfully for {project_name}",
            result={
                "project_name": project_name,
                "local_path": str(local_path),
                "function_count": function_count,
            },
        )

        logger.info(
            f"Local repo task completed: {project_name} ({function_count} functions)"
        )

    except asyncio.CancelledError:
        logger.info(f"Local repo task cancelled: {task_id}")
        await task_manager.update_task(
            task_id,
            status=TaskStatus.CANCELLED,
            status_message="Task was cancelled",
        )
        raise

    except Exception as e:
        logger.error(f"Local repo task failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            status_message=f"Failed: {str(e)}",
        )

    finally:
        task_manager.unregister_task(task_id)


def _generate_unique_project_name(base_name: str, ingestor) -> str:
    """
    Generate a unique project name by adding numeric suffix if needed.

    Args:
        base_name: The base project name (e.g., directory name)
        ingestor: MemgraphIngestor instance to check existence

    Returns:
        A unique project name (e.g., "myproject" or "myproject-2")
    """
    # Try base name first
    if not ingestor.project_exists(base_name):
        return base_name

    # Add numeric suffix
    for i in range(2, 100):
        candidate = f"{base_name}-{i}"
        if not ingestor.project_exists(candidate):
            return candidate

    # Fallback: use timestamp
    import time

    return f"{base_name}-{int(time.time())}"


@router.get(
    "/list-subdirectories",
    response_model=ListSubdirectoriesResponse,
    summary="List Subdirectories",
    description="List subdirectories in a given path for batch repository addition.",
)
async def list_subdirectories(
    path: str = Query(..., description="Base directory path to scan"),
) -> ListSubdirectoriesResponse:
    """
    List subdirectories in a given path.

    Fast scan mode - only checks depth=1 for immediate files and subdirectories.
    No recursive scanning or file counting for maximum performance.

    Returns information about each subdirectory including:
    - Whether it's a Python package (has __init__.py)
    - Whether it contains Python files at depth 1
    - Whether it contains any files at depth 1
    """
    base_path = Path(path).resolve()

    if not base_path.exists():
        raise HTTPException(status_code=404, detail=f"Path does not exist: {path}")

    if not base_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")

    subdirectories = []

    # Common non-project directories to skip
    SKIP_DIRS = {
        "__pycache__",
        "node_modules",
        ".git",
        "dist",
        "build",
        ".egg-info",
        ".dist-info",
        ".egg",
        ".pytest_cache",
        "__phello__",
        "__hello__",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".env",
        ".mypy_cache",
        ".ruff_cache",
        "htmlcov",
        ".coverage",
        ".hypothesis",
    }

    # Metadata/config-only directories (no real code)
    METADATA_ONLY_PATTERNS = {
        ".dist-info",
        ".egg-info",
        ".metadata",
    }

    try:
        for item in sorted(base_path.iterdir()):
            if not item.is_dir():
                continue

            # Skip hidden directories and common non-project directories
            if item.name.startswith(".") or item.name.startswith("_"):
                # Allow some important ones like .github, but skip most
                if item.name not in {".github", ".gitlab", ".gitignore"}:
                    continue

            if item.name in SKIP_DIRS:
                continue

            # Skip metadata-only directories
            if any(item.name.endswith(pattern) for pattern in METADATA_ONLY_PATTERNS):
                continue

            # Fast scan: only check depth=1
            has_init = (item / "__init__.py").exists()
            has_python = False
            has_files = False
            has_subdirs = False

            try:
                # Only scan immediate children (depth=1), no recursion
                for f in item.iterdir():
                    if f.is_file():
                        has_files = True
                        if f.suffix == ".py":
                            has_python = True
                    elif f.is_dir():
                        has_subdirs = True
                    # Early exit once we know what we need
                    if has_python:
                        break
            except PermissionError:
                pass

            # Include directories that are:
            # - A Python package (has __init__.py)
            # - Have Python files at depth 1
            # - Have subdirectories (likely namespace packages)
            # - Have any files at depth 1
            if not has_init and not has_python and not has_subdirs and not has_files:
                continue

            subdirectories.append(
                SubdirectoryInfo(
                    name=item.name,
                    path=str(item),
                    file_count=1 if has_files else 0,  # Just indicate presence of files
                    has_python=has_python,
                    has_init=has_init,
                )
            )

    except PermissionError:
        raise HTTPException(
            status_code=403, detail=f"Permission denied accessing: {path}"
        )

    return ListSubdirectoriesResponse(
        base_path=str(base_path),
        subdirectories=subdirectories,
        total=len(subdirectories),
    )


@router.post(
    "/add-multiple-local",
    response_model=dict,
    summary="Add Local Repository with Subdirectory Filter",
    description="Add a local repository with optional subdirectory filtering.",
)
async def add_local_repo_with_subdirs(request: AddLocalRepoWithSubdirsRequest) -> dict:
    """
    Add a local repository as a single project, with optional subdirectory filtering.

    This allows creating a project from a base directory while only processing
    selected subdirectories (e.g., selecting specific packages from site-packages).

    Returns task ID for tracking.
    """
    task_manager = get_task_manager()

    base_path = Path(request.local_path).resolve()

    if not base_path.exists() or not base_path.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Directory not found: {request.local_path}"
        )

    try:
        # Create task for this project
        task_id = await task_manager.create_task(
            task_type=TaskType.GRAPH_BUILD,
            repo_name=request.project_name,
            initial_message=f"Building graph for {request.project_name}",
        )

        # Start background task
        asyncio.create_task(
            process_add_local_repo_task(
                task_id=task_id,
                project_name=request.project_name,
                local_path=base_path,
                skip_embeddings=request.skip_embeddings or False,
                subdirs=request.subdirs if request.subdirs else None,
            )
        )

        return {
            "success": True,
            "job_id": task_id,
            "project_name": request.project_name,
            "local_path": str(base_path),
            "subdirs_count": len(request.subdirs) if request.subdirs else 0,
            "message": f"Started building graph for {request.project_name}"
            + (
                f" with {len(request.subdirs)} subdirectories"
                if request.subdirs
                else ""
            ),
        }

    except Exception as e:
        logger.error(f"Failed to start task for {request.project_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start task: {str(e)}")


@router.post(
    "/add-local",
    response_model=AddRepoResponse,
    summary="Add Local Repository",
    description="Build knowledge graph from a local directory (without cloning).",
)
async def add_local_repository(request: AddLocalRepoRequest) -> AddRepoResponse:
    """
    Add a local repository to AtCode.

    This endpoint allows building a knowledge graph directly from a local
    directory path, without needing to clone from a remote repository.

    Use cases:
    - Local development projects
    - Projects already on disk
    - Faster iteration during development

    Conflict handling:
    - If project_name is specified and already exists: returns 409 error
    - If project_name is not specified: auto-generates unique name (e.g., "proj-2")

    In replication mode (REPLICA server), the request is proxied to the MAIN server.
    """
    # In replication mode on REPLICA: proxy to MAIN server
    if settings.is_replica_node:
        logger.info("REPLICA mode: proxying add local repo request to MAIN server")
        return await _proxy_add_local_repo_to_main(request)

    try:
        local_path = Path(request.local_path).resolve()

        # Validate path
        if not local_path.exists():
            raise HTTPException(
                status_code=404, detail=f"Path does not exist: {request.local_path}"
            )

        if not local_path.is_dir():
            raise HTTPException(
                status_code=400, detail=f"Path is not a directory: {request.local_path}"
            )

        # Determine project name with conflict handling
        user_specified_name = bool(request.project_name)
        base_name = request.project_name or local_path.name

        # Use context manager for safe connection handling
        from graph.service import MemgraphIngestor

        host, port = settings.get_write_connection()

        with MemgraphIngestor(host=host, port=port) as ingestor:
            if user_specified_name:
                # User specified a name - check for conflict
                if ingestor.project_exists(base_name):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Project '{base_name}' already exists. Please choose a different name or omit project_name to auto-generate.",
                    )
                project_name = base_name
            else:
                # Auto-generate unique name
                project_name = _generate_unique_project_name(base_name, ingestor)
                if project_name != base_name:
                    logger.info(
                        f"Auto-generated unique project name: {base_name} -> {project_name}"
                    )

        # Create task
        task_manager = get_task_manager()
        task_id = await task_manager.create_task(
            task_type=TaskType.GRAPH_BUILD.value,
            repo_name=project_name,
            initial_message=f"Adding local repository {project_name}...",
        )

        # Start background processing
        background_task = asyncio.create_task(
            process_add_local_repo_task(
                task_id=task_id,
                project_name=project_name,
                local_path=local_path,
                skip_embeddings=request.skip_embeddings or False,
            )
        )
        task_manager.register_task(task_id, background_task)

        logger.info(
            f"Started local repo task: {task_id} for {project_name} at {local_path}"
        )

        return AddRepoResponse(
            success=True,
            task_id=task_id,
            repo_name=project_name,
            message=f"Local repository addition started. Track progress with task ID: {task_id}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start add local repository: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _proxy_add_local_repo_to_main(
    request: AddLocalRepoRequest,
) -> AddRepoResponse:
    """
    Proxy the add local repo request to the MAIN server.

    Note: This requires the local path to be accessible from the MAIN server,
    typically via a shared filesystem (NFS, etc.).
    """
    import httpx

    main_host = settings.MEMGRAPH_MAIN_HOST
    main_api_url = f"http://{main_host}:8005/api/repos/add-local"

    request_data = {
        "local_path": request.local_path,
    }
    if request.project_name:
        request_data["project_name"] = request.project_name
    if request.skip_embeddings:
        request_data["skip_embeddings"] = request.skip_embeddings

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(main_api_url, json=request_data)

            if response.status_code == 200:
                data = response.json()
                logger.info(
                    f"Add local repo request proxied to MAIN, task_id: {data.get('task_id')}"
                )
                return AddRepoResponse(
                    success=data.get("success", True),
                    task_id=data.get("task_id", ""),
                    repo_name=data.get("repo_name", ""),
                    message=f"Request delegated to MAIN server ({main_host}). {data.get('message', '')}",
                )
            else:
                error_detail = response.text
                logger.error(
                    f"MAIN server returned error: {response.status_code} - {error_detail}"
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"MAIN server error: {error_detail}",
                )

    except httpx.ConnectError as e:
        logger.error(f"Cannot connect to MAIN server at {main_api_url}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to MAIN server at {main_host}. Is it running?",
        )
    except httpx.TimeoutException as e:
        logger.error(f"Timeout connecting to MAIN server: {e}")
        raise HTTPException(
            status_code=504, detail=f"Timeout connecting to MAIN server at {main_host}"
        )


async def _proxy_add_repo_to_main(request: AddRepoRequest) -> AddRepoResponse:
    """
    Proxy the add repo request to the MAIN server.

    This allows REPLICA servers to delegate CPU-intensive builds to MAIN,
    keeping REPLICA responsive for user queries.
    """
    import httpx

    main_host = settings.MEMGRAPH_MAIN_HOST
    main_api_url = f"http://{main_host}:8005/api/repos/add"

    # Prepare request body (don't include credentials in logs)
    request_data = {
        "repo_url": request.repo_url,
    }
    if request.branch:
        request_data["branch"] = request.branch
    if request.username:
        request_data["username"] = request.username
    if request.password:
        request_data["password"] = request.password
    if request.skip_embeddings:
        request_data["skip_embeddings"] = request.skip_embeddings

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(main_api_url, json=request_data)

            if response.status_code == 200:
                data = response.json()
                logger.info(
                    f"Add repo request proxied to MAIN, task_id: {data.get('task_id')}"
                )
                return AddRepoResponse(
                    success=data.get("success", True),
                    task_id=data.get("task_id", ""),
                    repo_name=data.get("repo_name", ""),
                    message=f"Request delegated to MAIN server ({main_host}). {data.get('message', '')}",
                )
            elif response.status_code == 409:
                # Repo already exists
                raise HTTPException(
                    status_code=409,
                    detail=response.json().get("detail", "Repository already exists"),
                )
            else:
                error_detail = response.text
                logger.error(
                    f"MAIN server returned error: {response.status_code} - {error_detail}"
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"MAIN server error: {error_detail}",
                )

    except httpx.ConnectError as e:
        logger.error(f"Cannot connect to MAIN server at {main_api_url}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to MAIN server at {main_host}. Is it running?",
        )
    except httpx.TimeoutException as e:
        logger.error(f"Timeout connecting to MAIN server: {e}")
        raise HTTPException(
            status_code=504, detail=f"Timeout connecting to MAIN server at {main_host}"
        )


@router.get(
    "/list",
    response_model=RepoListResponse,
    summary="List Repositories",
    description="List all repositories available in AtCode.",
)
async def list_repositories() -> RepoListResponse:
    """
    List all repositories.

    Returns repositories from both wiki_repos (cloned) and wiki_doc (with docs).
    """
    return await _list_repositories_impl()


@router.get(
    "",
    response_model=RepoListResponse,
    summary="List Repositories",
    description="List all repositories available in AtCode (alias for /list).",
)
@router.get(
    "/",
    response_model=RepoListResponse,
    summary="List Repositories",
    description="List all repositories available in AtCode (alias for /list).",
)
async def list_repositories_alias() -> RepoListResponse:
    """Alias for /api/repos/list - provides cleaner URL."""
    return await _list_repositories_impl()


_repos_cache: dict = {"data": None}


def invalidate_repos_cache():
    """Invalidate the in-memory repos list cache (call after add/delete/build)."""
    _repos_cache["data"] = None


async def _list_repositories_impl() -> RepoListResponse:
    """
    Implementation for listing repositories.

    Uses a permanent in-memory cache that is only invalidated by mutation
    operations (add/delete repo, graph build complete).  This avoids
    querying Memgraph on every page load.

    Logic:
    1. wiki_repos directories: ALWAYS show (even if no graph data), unless directory deleted
    2. Other locations (local repos): ONLY show if graph data exists in Memgraph
    3. wiki_doc: Add documentation info to existing repos
    """
    if _repos_cache["data"] is not None:
        return _repos_cache["data"]

    try:
        repos = {}
        memgraph_query_succeeded = False
        wiki_repos_path = get_wiki_repos_path()
        wiki_doc_path = get_wiki_doc_path()

        # Step 1: wiki_repos directories - ALWAYS show (even without graph data)
        if wiki_repos_path.exists():
            for item in wiki_repos_path.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    repos[item.name] = RepoInfo(
                        name=item.name,
                        has_graph=False,  # Will check in Step 2
                        has_docs=False,
                        research_count=0,
                        path=str(item),
                    )

        # Step 2: Check Memgraph for graph data and add local repos
        memgraph_projects = set()
        try:
            from api.routes.graph import get_ingestor

            with get_ingestor() as ingestor:
                # Get all projects from Memgraph
                projects = ingestor._execute_query(
                    "MATCH (p:Project) RETURN p.name AS name, p.path AS path ORDER BY p.name"
                )
                memgraph_query_succeeded = True

                for project in projects:
                    name = project["name"]
                    path = project.get("path", "")
                    memgraph_projects.add(name)

                    if name in repos:
                        # wiki_repos repo: update has_graph status
                        repos[name].has_graph = True
                        # Update path if Memgraph has more accurate info
                        if path:
                            repos[name].path = path
                    else:
                        # Local repo (not in wiki_repos): only show if path exists
                        if path and Path(path).exists():
                            repos[name] = RepoInfo(
                                name=name,
                                has_graph=True,
                                has_docs=False,
                                research_count=0,
                                path=path,
                            )
                        # If path doesn't exist, don't show this repo (stale Memgraph data)
        except Exception as e:
            logger.warning(f"Failed to fetch projects from Memgraph: {e}")

        # Step 3: Check wiki_doc for documentation - only add to repos if already in repos
        # This prevents showing deleted local repos that only have docs left
        if wiki_doc_path.exists():
            for item in wiki_doc_path.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    # Only add if this repo is already in our list (from wiki_repos or Memgraph)
                    if item.name in repos:
                        # Count research docs (JSON files in the root, excluding _meta.json, _index.json)
                        research_count = sum(
                            1
                            for f in item.iterdir()
                            if f.is_file()
                            and f.suffix == ".json"
                            and not f.name.startswith("_")
                        )
                        repos[item.name].has_docs = True
                        repos[item.name].research_count = research_count
                    # If not in repos, skip it (deleted local repo with stale docs)

        repo_list = list(repos.values())
        repo_list.sort(key=lambda r: r.name)

        result = RepoListResponse(repos=repo_list, total=len(repo_list))
        # Avoid permanently caching a degraded view if Memgraph was still
        # starting up or had a transient connection issue during the first load.
        if memgraph_query_succeeded:
            _repos_cache["data"] = result
            _repos_cache["ts"] = time.monotonic()
        return result

    except Exception as e:
        logger.error(f"Failed to list repositories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{repo_name}",
    summary="Delete Repository",
    description="Delete a repository and its associated data.",
)
async def delete_repository(
    repo_name: str,
    delete_docs: bool = Query(False, description="Also delete generated documentation"),
    delete_graph: bool = Query(
        True, description="Also delete knowledge graph data (default: True)"
    ),
) -> dict:
    """
    Delete a repository.

    Args:
        repo_name: Name of the repository to delete
        delete_docs: Also delete documentation in wiki_doc
        delete_graph: Also delete knowledge graph (default: True)
    """
    try:
        import shutil

        deleted = []

        # Delete from wiki_repos
        wiki_repos_path = get_wiki_repos_path() / repo_name
        if wiki_repos_path.exists():
            shutil.rmtree(wiki_repos_path)
            deleted.append("repository")
            logger.info(f"Deleted repository: {wiki_repos_path}")

        # Optionally delete documentation
        if delete_docs:
            wiki_doc_path = get_wiki_doc_path() / repo_name
            if wiki_doc_path.exists():
                shutil.rmtree(wiki_doc_path)
                deleted.append("documentation")
                logger.info(f"Deleted documentation: {wiki_doc_path}")

        # Delete graph data (default: True for local repos)
        if delete_graph:
            try:
                from core.config import settings
                from graph.service import MemgraphIngestor

                with MemgraphIngestor(
                    host=settings.MEMGRAPH_HOST,
                    port=settings.MEMGRAPH_PORT,
                ) as ingestor:
                    # Check if project exists in graph
                    project_exists = ingestor._execute_query(
                        "MATCH (p:Project {name: $project_name}) RETURN count(p) AS count",
                        {"project_name": repo_name},
                    )

                    if project_exists and project_exists[0]["count"] > 0:
                        ingestor.clean_project(repo_name)
                        deleted.append("knowledge_graph")
                        logger.info(f"Deleted knowledge graph for: {repo_name}")
            except Exception as e:
                logger.warning(f"Could not delete graph data: {e}")

        if not deleted:
            raise HTTPException(
                status_code=404, detail=f"Repository not found: {repo_name}"
            )

        invalidate_repos_cache()
        return {
            "success": True,
            "repo_name": repo_name,
            "deleted": deleted,
            "message": f"Deleted: {', '.join(deleted)}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete repository: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/sync",
    response_model=dict,
    summary="Synchronize Repositories",
    description="Sync repository state between filesystem and Memgraph. Removes orphaned projects.",
)
async def sync_repositories(
    dry_run: bool = Query(
        False, description="If true, only report what would be deleted"
    ),
    remove_orphaned: bool = Query(
        True, description="Remove projects that exist in Memgraph but not in filesystem"
    ),
) -> dict:
    """
    Synchronize repository state between filesystem and Memgraph.

    This helps clean up orphaned projects that exist in Memgraph but not in wiki_repos.
    """
    try:
        from core.config import settings
        from graph.service import MemgraphIngestor

        wiki_repos_path = get_wiki_repos_path()

        # Get all valid repos from wiki_repos
        valid_repos = set()
        if wiki_repos_path.exists():
            for item in wiki_repos_path.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    valid_repos.add(item.name)

        # Get all projects from Memgraph
        orphaned = []
        kept = []

        with MemgraphIngestor(
            host=settings.MEMGRAPH_HOST,
            port=settings.MEMGRAPH_PORT,
        ) as ingestor:
            projects = ingestor._execute_query(
                "MATCH (p:Project) RETURN p.name AS name, p.path AS path ORDER BY p.name"
            )

            for project in projects:
                name = project["name"]
                path = project.get("path", "")

                # Check if this is a local repo (not in wiki_repos)
                is_local_repo = path and not path.startswith(str(wiki_repos_path))

                if is_local_repo:
                    # Local repos are kept if the path still exists
                    from pathlib import Path

                    if Path(path).exists():
                        kept.append(
                            {"name": name, "path": path, "reason": "local_repo_exists"}
                        )
                    else:
                        orphaned.append(
                            {"name": name, "path": path, "reason": "local_repo_missing"}
                        )
                elif name not in valid_repos:
                    # Repo in Memgraph but not in wiki_repos (and not local)
                    orphaned.append(
                        {"name": name, "path": path, "reason": "not_in_wiki_repos"}
                    )
                else:
                    kept.append({"name": name, "path": path, "reason": "in_wiki_repos"})

            # Delete orphaned projects
            deleted = []
            if remove_orphaned and orphaned and not dry_run:
                for item in orphaned:
                    try:
                        ingestor.clean_project(item["name"])
                        deleted.append(item["name"])
                        logger.info(
                            f"Deleted orphaned project: {item['name']} ({item['reason']})"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to delete orphaned project {item['name']}: {e}"
                        )

        return {
            "success": True,
            "dry_run": dry_run,
            "kept": kept,
            "orphaned": orphaned,
            "deleted": deleted,
            "summary": f"Kept {len(kept)} projects, found {len(orphaned)} orphaned, deleted {len(deleted)}",
        }

    except Exception as e:
        logger.error(f"Failed to sync repositories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
