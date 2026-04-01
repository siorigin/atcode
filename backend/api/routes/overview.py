# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import contextlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from agent.orchestrators.doc_run_store import DocRunStore
from api.middleware.auth import get_current_user
from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# Request/Response Models


class GenerateOverviewRequest(BaseModel):
    """Request model for generating overview documentation."""

    language: str = Field(
        default="en", description="Output language: 'en' for English, 'zh' for Chinese"
    )
    doc_depth: int = Field(
        default=2, ge=0, le=4, description="Maximum depth for documentation hierarchy"
    )
    mode: str = Field(
        default="overview",
        description="Documentation mode: 'overview' (fast, architecture-focused) or 'detailed' (comprehensive)",
    )
    focus: str | None = Field(
        default=None,
        description="Optional focus area for documentation. Full repo is covered, but emphasis is adjusted based on this focus.",
    )
    model: str | None = Field(
        default=None,
        description="Optional model to use for generation (e.g., 'claude-opus-4-5-20251101', 'gpt-5.2'). If not specified, uses default orchestrator config.",
    )
    paper_id: str | None = Field(
        default=None,
        description="Optional paper ID to inject paper context into documentation generation. When provided, paper metadata (title, abstract) is used to enrich focus areas.",
    )


class VersionInfo(BaseModel):
    """Information about a documentation version."""

    version_id: str
    mode: str
    doc_depth: int
    generated_at: str
    statistics: dict | None = None


class OverviewStatusResponse(BaseModel):
    """Response model for overview status check."""

    exists: bool
    repo: str
    version: str | None = None
    version_id: str | None = None
    mode: str | None = None
    generated_at: str | None = None
    statistics: dict | None = None
    has_overview_md: bool | None = None
    doc_depth: int | None = None
    versions: list[VersionInfo] | None = None  # All available versions
    error: str | None = None


class TaskStatusResponse(BaseModel):
    """Response model for task status."""

    task_id: str
    status: str
    progress: int
    step: str
    status_message: str
    result: dict | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    trajectory: list[dict] = Field(
        default_factory=list,
        description="Recent task trajectory events for debugging/progress inspection",
    )


class GenerateOverviewResponse(BaseModel):
    """Response model for starting documentation generation."""

    task_id: str
    status: str = "pending"
    message: str = "Documentation generation enqueued"


def get_wiki_doc_path() -> Path:
    """Get the wiki_doc path (in data directory)."""
    from core.config import get_wiki_doc_dir

    return get_wiki_doc_dir()


def get_doc_run_store(repo: str, task_id: str) -> DocRunStore:
    """Get local run artifact store for one documentation task."""
    return DocRunStore(get_wiki_doc_path(), repo, task_id)


def get_overview_orchestrator(
    doc_depth: int = 2, mode: str = "detailed", model: str | None = None
):
    """Get or create DocOrchestrator instance with specified depth, mode, and optional model."""
    from agent.orchestrators.doc import DocOrchestrator
    from core.config import ModelConfig, settings

    # Build model_config if a specific model was requested
    model_config = None
    if model:
        # Get base config from settings and override model_id
        base_config = settings.active_orchestrator_config
        model_config = ModelConfig(
            provider=base_config.provider,
            model_id=model,
            api_key=base_config.api_key,
            endpoint=base_config.endpoint,
            project_id=base_config.project_id,
            region=base_config.region,
            provider_type=base_config.provider_type,
            thinking_budget=base_config.thinking_budget,
            service_account_file=base_config.service_account_file,
        )
        logger.info(f"Using custom model for generation: {model}")

    orchestrator = DocOrchestrator(
        doc_depth=doc_depth, mode=mode, model_config=model_config
    )
    logger.info(
        f"✅ Initialized DocOrchestrator (doc_depth={doc_depth}, mode={mode}, model={model or 'default'})"
    )
    return orchestrator


def get_memgraph_ingestor():
    """Get or create MemgraphIngestor instance."""
    from core.config import settings
    from graph.service import MemgraphIngestor

    if not hasattr(get_memgraph_ingestor, "_instance"):
        ingestor = MemgraphIngestor(
            host=settings.MEMGRAPH_HOST,
            port=settings.MEMGRAPH_PORT,
            batch_size=settings.resolve_batch_size(None),
        )
        ingestor.__enter__()
        get_memgraph_ingestor._instance = ingestor
        logger.info(
            f"✅ Initialized MemgraphIngestor: {settings.MEMGRAPH_HOST}:{settings.MEMGRAPH_PORT}"
        )
    return get_memgraph_ingestor._instance


async def _generate_overview_background(
    task_id: str,
    repo: str,
    language: str = "en",
    doc_depth: int = 2,
    mode: str = "overview",
    focus: str | None = None,
    model: str | None = None,
    paper_id: str | None = None,
    resume: bool = False,
) -> None:
    """
    Background task for documentation generation.

    This function runs in the background and doesn't block the HTTP response.
    It updates the task status as generation progresses.

    Args:
        task_id: Task identifier
        repo: Repository name
        language: Output language ('en' or 'zh')
        doc_depth: Maximum documentation depth (0-4)
        mode: Documentation mode ('overview' or 'detailed')
        focus: Optional focus area for emphasis in documentation
        model: Optional model ID to use for generation
        paper_id: Optional paper ID to inject paper context into focus areas
    """
    task_manager = get_task_manager()
    inactivity_timeout_seconds = max(
        0.01, float(os.getenv("OVERVIEW_PROGRESS_TIMEOUT_SECONDS", "1800"))
    )
    heartbeat_interval_seconds = max(
        0.01,
        min(
            inactivity_timeout_seconds,
            float(os.getenv("OVERVIEW_PROGRESS_HEARTBEAT_SECONDS", "5")),
        ),
    )
    auto_resume_on_stall = os.getenv(
        "OVERVIEW_AUTO_RESUME_ON_STALL", "true"
    ).lower() not in {"0", "false", "no", "off"}
    max_auto_resume_attempts = max(
        0, int(os.getenv("OVERVIEW_AUTO_RESUME_MAX_ATTEMPTS", "3"))
    )
    wiki_doc_path = get_wiki_doc_path()
    run_store: DocRunStore | None = None
    request_payload = {
        "task_id": task_id,
        "repo": repo,
        "language": language,
        "doc_depth": doc_depth,
        "mode": mode,
        "focus": focus or "",
        "model": model or "",
        "paper_id": paper_id or "",
        "resume_supported": True,
    }

    try:
        run_store = get_doc_run_store(repo, task_id)
        existing_request = run_store.load_request()
        if existing_request:
            resume_count = int(existing_request.get("resume_count", 0))
            if resume:
                request_payload["resume_count"] = resume_count + 1
                request_payload["last_resumed_at"] = datetime.now(UTC).isoformat()
            else:
                request_payload["resume_count"] = resume_count
        else:
            request_payload["resume_count"] = 0
        run_store.save_request({**(existing_request or {}), **request_payload})
    except Exception as e:
        logger.warning(
            f"Failed to initialize local doc run store for task {task_id}: {e}"
        )
        run_store = None

    def record_local_event(
        *,
        task_status: str,
        progress: int,
        step: str,
        message: str,
        event_type: str = "status",
        error: str | None = None,
        details: dict | None = None,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "task_id": task_id,
            "repo": repo,
            "task_status": task_status,
            "event_type": event_type,
            "progress": progress,
            "step": step,
            "message": message,
            "error": error,
            "details": details,
            "resume_mode": resume,
        }
        if extra:
            payload.update(extra)
        if run_store is not None:
            run_store.save_status(payload)
            run_store.append_trajectory_event(payload)

    def checkpoint_callback(state: dict) -> None:
        if run_store is not None:
            run_store.save_checkpoint(
                state,
                metadata={
                    "repo": repo,
                    "language": language,
                    "doc_depth": doc_depth,
                    "mode": mode,
                    "focus": focus or "",
                    "model": model or "",
                    "paper_id": paper_id or "",
                    "resume_mode": resume,
                },
            )

    def build_stall_error(
        *,
        last_event_at: datetime,
        step: str,
        progress: int,
        message: str,
    ) -> TimeoutError:
        stalled_for = (datetime.now(UTC) - last_event_at).total_seconds()
        return TimeoutError(
            "Documentation generation stalled for "
            f"{stalled_for:.1f}s without a new event "
            f"(last_step={step or 'unknown'}, "
            f"progress={progress}%, "
            f"last_update_at={last_event_at.isoformat()}, "
            f"last_message={message!r})"
        )

    def build_heartbeat_message(
        *,
        step: str,
        message: str,
        details: dict | None,
        seconds_since_real_event: float,
    ) -> str:
        wait_text = f"{seconds_since_real_event:.0f}s since last update"
        if step == "children_working":
            completed = details.get("completed_section_count") if details else None
            total = details.get("outline_count") if details else None
            if isinstance(completed, int) and isinstance(total, int) and total > 0:
                return (
                    "Child agents still generating sections... "
                    f"({completed}/{total} completed, waiting {wait_text})"
                )
            return f"Child agents still generating sections... (waiting {wait_text})"
        if step == "resuming_section":
            section_title = details.get("current_section_title") if details else None
            if section_title:
                return (
                    f"Still resuming section: {section_title} "
                    f"(waiting {wait_text})"
                )
        base = (message or "Documentation generation still in progress").rstrip(". ")
        return f"{base} (waiting {wait_text})"

    try:
        existing_state = await task_manager.get_task_status(task_id)
        previous_status = ""
        if isinstance(existing_state, dict):
            previous_status = str(existing_state.get("status", "") or "")
        elif existing_state is not None:
            status_value = getattr(existing_state, "status", None)
            previous_status = (
                status_value.value if hasattr(status_value, "value") else str(status_value or "")
            )
        init_step = "resuming" if resume else "initializing"
        init_message = (
            f"Resuming documentation generation for {repo}"
            if resume
            else f"Starting documentation generation for {repo}"
        )
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=5,
            step=init_step,
            status_message=init_message,
            error="",
            details={
                "repo": repo,
                "doc_depth": doc_depth,
                "language": language,
                "mode": mode,
                "focus_provided": bool(focus),
                "paper_id": paper_id,
                "model": model or "",
                "resume_mode": resume,
                "previous_status": previous_status,
            },
        )
        record_local_event(
            task_status=TaskStatus.RUNNING.value,
            progress=5,
            step=init_step,
            message=init_message,
            details={
                "repo": repo,
                "doc_depth": doc_depth,
                "language": language,
                "mode": mode,
                "resume_mode": resume,
            },
        )

        ingestor = get_memgraph_ingestor()
        if not ingestor:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error="Database connection not available",
            )
            record_local_event(
                task_status=TaskStatus.FAILED.value,
                progress=5,
                step="initializing",
                message="Database connection not available",
                event_type="error",
                error="Database connection not available",
            )
            return

        repo_doc_path = wiki_doc_path / repo
        doc_root_writable = os.access(wiki_doc_path, os.W_OK)
        repo_dir_writable = (not repo_doc_path.exists()) or os.access(repo_doc_path, os.W_OK)
        if not doc_root_writable or not repo_dir_writable:
            permission_error = (
                "Documentation storage is not writable inside the backend container. "
                f"Check permissions for {wiki_doc_path} and {repo_doc_path}."
            )
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                progress=0,
                step="permission_error",
                status_message=permission_error,
                error=permission_error,
                details={
                    "wiki_doc_path": str(wiki_doc_path),
                    "repo_doc_path": str(repo_doc_path),
                    "doc_root_writable": doc_root_writable,
                    "repo_dir_writable": repo_dir_writable,
                },
            )
            record_local_event(
                task_status=TaskStatus.FAILED.value,
                progress=0,
                step="permission_error",
                message=permission_error,
                event_type="error",
                error=permission_error,
                details={
                    "wiki_doc_path": str(wiki_doc_path),
                    "repo_doc_path": str(repo_doc_path),
                },
            )
            return

        orchestrator = get_overview_orchestrator(
            doc_depth=doc_depth, mode=mode, model=model
        )

        effective_focus = focus
        if paper_id:
            try:
                from paper.downloader import PaperDownloader

                paper_data = PaperDownloader().get_paper(paper_id)
                if paper_data:
                    title = paper_data.get("title", "")
                    abstract = (paper_data.get("abstract", "") or "")[:500]
                    paper_context = (
                        f"\n\n[PAPER CONTEXT] This repository implements the paper: "
                        f'"{title}"\n'
                        f"Abstract: {abstract}\n"
                        f"When documenting code components, relate them to paper concepts where relevant. "
                        f"Highlight the paper-code mapping (e.g., which class implements which algorithm from the paper)."
                    )
                    effective_focus = (focus or "") + paper_context
                    logger.info(
                        f"Injected paper context for paper_id={paper_id}: {title}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load paper context for {paper_id}: {e}")

        checkpoint_payload = run_store.load_checkpoint()
        if resume:
            if not checkpoint_payload or not checkpoint_payload.get("state"):
                raise FileNotFoundError(
                    f"No checkpoint available for task {task_id}; cannot resume"
                )
        real_event_at = datetime.now(UTC)
        last_step = init_step
        last_progress = 5
        last_message = init_message
        last_details: dict | None = None
        auto_resume_attempts = 0
        heartbeat_steps = {
            "children_working",
            "resuming_sections",
            "resuming_section",
        }
        auto_resume_steps = set(heartbeat_steps)

        def create_generator(resume_mode: bool):
            checkpoint_payload_local = run_store.load_checkpoint() if run_store else None
            if resume_mode:
                if not checkpoint_payload_local or not checkpoint_payload_local.get(
                    "state"
                ):
                    raise FileNotFoundError(
                        f"No checkpoint available for task {task_id}; cannot resume"
                    )
                return orchestrator.stream_resume(
                    repo_name=repo,
                    ingestor=ingestor,
                    wiki_doc_path=wiki_doc_path,
                    checkpoint_state=checkpoint_payload_local["state"],
                    language=language,
                    focus_areas=effective_focus,
                    checkpoint_callback=checkpoint_callback,
                )
            return orchestrator.stream_generate(
                repo_name=repo,
                ingestor=ingestor,
                wiki_doc_path=wiki_doc_path,
                language=language,
                focus_areas=effective_focus,
                checkpoint_callback=checkpoint_callback,
            )

        async def consume_generator(generator, *, resume_mode: bool) -> None:
            nonlocal real_event_at
            nonlocal last_step
            nonlocal last_progress
            nonlocal last_message
            nonlocal last_details

            while True:
                elapsed = (datetime.now(UTC) - real_event_at).total_seconds()
                remaining = inactivity_timeout_seconds - elapsed
                if remaining <= 0:
                    raise build_stall_error(
                        last_event_at=real_event_at,
                        step=last_step,
                        progress=last_progress,
                        message=last_message,
                    )

                wait_timeout = remaining
                if last_step in heartbeat_steps:
                    wait_timeout = min(wait_timeout, heartbeat_interval_seconds)

                try:
                    event = await asyncio.wait_for(anext(generator), timeout=wait_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    stalled_for = (datetime.now(UTC) - real_event_at).total_seconds()
                    if (
                        last_step in heartbeat_steps
                        and stalled_for < inactivity_timeout_seconds
                    ):
                        heartbeat_message = build_heartbeat_message(
                            step=last_step,
                            message=last_message,
                            details=last_details,
                            seconds_since_real_event=stalled_for,
                        )
                        heartbeat_details = {
                            **(last_details or {}),
                            "heartbeat": True,
                            "resume_mode": resume_mode,
                            "seconds_since_last_real_event": round(stalled_for, 1),
                            "heartbeat_interval_seconds": heartbeat_interval_seconds,
                        }
                        await task_manager.update_task(
                            task_id,
                            status=TaskStatus.RUNNING,
                            progress=last_progress,
                            step=last_step,
                            status_message=heartbeat_message,
                            error="",
                            details=heartbeat_details,
                        )
                        record_local_event(
                            task_status=TaskStatus.RUNNING.value,
                            progress=last_progress,
                            step=last_step,
                            message=heartbeat_message,
                            details=heartbeat_details,
                        )
                        continue
                    raise build_stall_error(
                        last_event_at=real_event_at,
                        step=last_step,
                        progress=last_progress,
                        message=last_message,
                    ) from exc

                event_type = event.get("type")
                real_event_at = datetime.now(UTC)
                last_step = event.get("step", last_step)
                last_progress = event.get("progress", last_progress)
                last_message = event.get("content", last_message)
                if isinstance(event.get("details"), dict):
                    last_details = event.get("details")

                if event_type == "status":
                    await task_manager.update_task(
                        task_id,
                        progress=event.get("progress", 0),
                        step=event.get("step", ""),
                        status_message=event.get("content", ""),
                        details=event.get("details"),
                    )
                    record_local_event(
                        task_status=TaskStatus.RUNNING.value,
                        progress=event.get("progress", 0),
                        step=event.get("step", ""),
                        message=event.get("content", ""),
                        details=event.get("details"),
                    )

                elif event_type == "complete":
                    result = event.get("content", {})
                    await task_manager.update_task(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        progress=100,
                        step="complete",
                        status_message="Documentation generation completed",
                        result=result,
                        details=event.get("details"),
                    )
                    record_local_event(
                        task_status=TaskStatus.COMPLETED.value,
                        progress=100,
                        step="complete",
                        message="Documentation generation completed",
                        event_type="complete",
                        details=event.get("details"),
                        extra={"result": result},
                    )
                    logger.info(
                        f"Documentation generation completed: {task_id} for {repo}"
                    )

                elif event_type == "error":
                    error_message = event.get("content", "Unknown error")
                    await task_manager.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        error=error_message,
                    )
                    record_local_event(
                        task_status=TaskStatus.FAILED.value,
                        progress=last_progress,
                        step=last_step,
                        message=error_message,
                        event_type="error",
                        error=error_message,
                        details=event.get("details"),
                    )
                    logger.error(
                        f"Documentation generation failed: {task_id}, error: {error_message}"
                    )

        current_resume_mode = resume
        while True:
            generator = create_generator(current_resume_mode)
            try:
                await consume_generator(generator, resume_mode=current_resume_mode)
                break
            except TimeoutError as e:
                can_auto_resume = (
                    auto_resume_on_stall
                    and auto_resume_attempts < max_auto_resume_attempts
                    and last_step in auto_resume_steps
                    and run_store is not None
                    and bool(run_store.load_checkpoint())
                )
                if can_auto_resume:
                    auto_resume_attempts += 1
                    current_resume_mode = True
                    auto_resume_message = (
                        "Documentation stalled while waiting for section generation; "
                        "retrying missing sections from the latest checkpoint..."
                    )
                    auto_resume_details = {
                        **(last_details or {}),
                        "auto_resume": True,
                        "auto_resume_attempt": auto_resume_attempts,
                        "max_auto_resume_attempts": max_auto_resume_attempts,
                        "resume_mode": True,
                        "stalled_step": last_step,
                        "stalled_progress": last_progress,
                        "stalled_error": str(e),
                    }
                    await task_manager.update_task(
                        task_id,
                        status=TaskStatus.RUNNING,
                        progress=last_progress,
                        step="auto_resuming",
                        status_message=auto_resume_message,
                        error="",
                        details=auto_resume_details,
                    )
                    record_local_event(
                        task_status=TaskStatus.RUNNING.value,
                        progress=last_progress,
                        step="auto_resuming",
                        message=auto_resume_message,
                        details=auto_resume_details,
                    )
                    real_event_at = datetime.now(UTC)
                    last_step = "auto_resuming"
                    last_message = auto_resume_message
                    last_details = auto_resume_details
                    continue
                raise
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(generator.aclose(), timeout=1.0)

    except asyncio.CancelledError:
        logger.info(f"Documentation generation task cancelled: {task_id}")
        try:
            await task_manager.update_task(
                task_id, status=TaskStatus.CANCELLED, error="Task was cancelled by user"
            )
            record_local_event(
                task_status=TaskStatus.CANCELLED.value,
                progress=last_progress if "last_progress" in locals() else 0,
                step=last_step if "last_step" in locals() else "cancelled",
                message="Task was cancelled by user",
                event_type="cancelled",
                error="Task was cancelled by user",
            )
        except Exception as update_err:
            logger.error(f"Failed to update task cancelled state: {update_err}")
        raise

    except TimeoutError as e:
        logger.warning(
            f"Documentation generation stalled: {task_id}, error: {e}",
            exc_info=True,
        )
        try:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.STALLED,
                progress=last_progress if "last_progress" in locals() else 0,
                step=last_step if "last_step" in locals() else "stalled",
                status_message=last_message if "last_message" in locals() else "",
                error=str(e),
                details={
                    "resume_available": True,
                    "resume_mode": resume,
                    "last_step": last_step if "last_step" in locals() else "",
                    "last_progress": last_progress if "last_progress" in locals() else 0,
                },
            )
            record_local_event(
                task_status=TaskStatus.STALLED.value,
                progress=last_progress if "last_progress" in locals() else 0,
                step=last_step if "last_step" in locals() else "stalled",
                message=last_message if "last_message" in locals() else "Task stalled",
                event_type="stalled",
                error=str(e),
                details={
                    "resume_available": True,
                    "resume_mode": resume,
                },
            )
        except Exception as update_err:
            logger.error(f"Failed to update task stalled state: {update_err}")

    except Exception as e:
        logger.error(
            f"Background generation task failed: {task_id}, error: {e}", exc_info=True
        )
        try:
            await task_manager.update_task(
                task_id, status=TaskStatus.FAILED, error=str(e)
            )
            record_local_event(
                task_status=TaskStatus.FAILED.value,
                progress=last_progress if "last_progress" in locals() else 0,
                step=last_step if "last_step" in locals() else "failed",
                message=str(e),
                event_type="error",
                error=str(e),
            )
        except Exception as update_err:
            logger.error(f"Failed to update task error state: {update_err}")

    finally:
        task_manager.unregister_task(task_id)


@router.get("/{repo}/status", response_model=OverviewStatusResponse)
async def get_overview_status(
    repo: str,
    user_id: str = Depends(get_current_user),
):
    """
    Check if overview documentation exists for a repository.

    Returns metadata about the documentation if it exists, including all available versions.
    """
    try:
        wiki_doc_path = get_wiki_doc_path()
        repo_path = wiki_doc_path / repo
        meta_path = repo_path / "_meta.json"

        # First check _meta.json to determine current version
        current_version = None
        versions_list = []
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta_data = json.load(f)
                current_version = meta_data.get("current_version")
                versions_list = meta_data.get("versions", [])
                if not current_version and versions_list:
                    current_version = versions_list[-1].get("version_id")
            except Exception as e:
                logger.warning(f"Failed to read meta: {e}")

        # Determine paths based on current version
        if current_version:
            version_path = repo_path / "versions" / current_version
            overview_path = version_path / "overview.md"
            index_path = version_path / "_index.json"
        else:
            # Legacy fallback
            overview_path = repo_path / "overview.md"
            index_path = repo_path / "_index.json"

        exists = index_path.exists()  # Use index as indicator of existence
        status_data = {
            "exists": exists,
            "repo": repo,
            "version": "5.0" if exists else None,
            "version_id": current_version,
            "mode": None,
            "generated_at": None,
            "has_overview_md": overview_path.exists(),
            "doc_depth": None,
            "versions": None,
            "error": None,
        }

        # Load current version info from _index.json
        if exists and index_path.exists():
            try:
                with open(index_path) as f:
                    index_data = json.load(f)
                    status_data["generated_at"] = index_data.get("generated_at")
                    status_data["statistics"] = index_data.get("statistics")
                    status_data["doc_depth"] = index_data.get("doc_depth")
                    status_data["version"] = index_data.get("version", "5.0")
                    status_data["version_id"] = index_data.get("version_id")
                    status_data["mode"] = index_data.get("mode")
            except Exception as e:
                logger.warning(f"Failed to read index: {e}")

        # Load version list from _meta.json
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta_data = json.load(f)
                    versions = meta_data.get("versions", [])
                    # Sort by generated_at descending (newest first)
                    versions.sort(key=lambda v: v.get("generated_at", ""), reverse=True)
                    status_data["versions"] = [
                        VersionInfo(
                            version_id=v.get("version_id", ""),
                            mode=v.get("mode", "unknown"),
                            doc_depth=v.get("doc_depth", 2),
                            generated_at=v.get("generated_at", ""),
                            statistics=v.get("statistics"),
                        )
                        for v in versions
                    ]
            except Exception as e:
                logger.warning(f"Failed to read meta: {e}")

        return OverviewStatusResponse(**status_data)
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return OverviewStatusResponse(exists=False, repo=repo, error=str(e))


@router.post("/{repo}/generate", response_model=GenerateOverviewResponse)
async def generate_overview(
    repo: str,
    request: GenerateOverviewRequest,
    user_id: str = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Enqueue documentation generation as a background task.

    Returns immediately with task_id. The generation continues in the background,
    even if the client disconnects.

    Response:
    {
        "task_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "status": "pending",
        "message": "Documentation generation enqueued"
    }

    Use the task_id to:
    - GET /{repo}/generate/{task_id} - Get current status
    - GET /{repo}/generate/{task_id}/stream - Stream live updates
    """
    task_manager = get_task_manager()

    try:
        # Always use detailed mode (overview mode removed)
        mode = "detailed"

        # Create background task with task type and repo info
        task_id = await task_manager.create_task(
            task_type=TaskType.OVERVIEW_GEN.value,
            repo_name=repo,
            user_id=user_id,
            initial_message=f"Enqueued documentation generation for {repo} (depth={request.doc_depth}, mode={mode})",
        )

        # Queue the background task
        # Create an asyncio task directly instead of using BackgroundTasks
        # which doesn't properly handle async functions
        background_task = asyncio.create_task(
            _generate_overview_background(
                task_id=task_id,
                repo=repo,
                language=request.language,
                doc_depth=request.doc_depth,
                mode=mode,
                focus=request.focus,
                model=request.model,
                paper_id=request.paper_id,
            )
        )
        # Register the task so it can be cancelled
        task_manager.register_task(task_id, background_task)

        logger.info(
            f"Enqueued documentation generation: {task_id} for {repo} (mode={mode})"
        )

        return GenerateOverviewResponse(task_id=task_id)

    except Exception as e:
        logger.error(f"Failed to enqueue documentation generation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to enqueue task: {e}")


@router.get("/{repo}/generate/{task_id}", response_model=TaskStatusResponse)
async def get_generation_status(
    repo: str,
    task_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Get the status of a documentation generation task.

    Returns current progress, status, and any error messages.
    """
    task_manager = get_task_manager()

    task_state = await task_manager.get_task_status(task_id)
    if not task_state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return TaskStatusResponse(
        task_id=task_state.task_id,
        status=task_state.status.value,
        progress=task_state.progress,
        step=task_state.step,
        status_message=task_state.status_message,
        result=task_state.result,
        error=task_state.error,
        created_at=task_state.created_at,
        started_at=task_state.started_at,
        completed_at=task_state.completed_at,
        trajectory=[
            asdict(event) if not isinstance(event, dict) else event
            for event in getattr(task_state, "trajectory", [])
        ],
    )


@router.post("/{repo}/generate/{task_id}/resume", response_model=GenerateOverviewResponse)
async def resume_generation(
    repo: str,
    task_id: str,
    user_id: str = Depends(get_current_user),
):
    """Resume a stalled documentation generation task from its latest checkpoint."""
    task_manager = get_task_manager()
    task_state = await task_manager.get_task_status(task_id)
    if not task_state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task_state.repo_name and task_state.repo_name != repo:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} belongs to repo {task_state.repo_name}, not {repo}",
        )

    status_value = (
        task_state.status.value
        if hasattr(task_state.status, "value")
        else str(task_state.status)
    )

    if status_value in [TaskStatus.RUNNING.value, TaskStatus.PENDING.value]:
        raise HTTPException(
            status_code=409, detail=f"Task {task_id} is already {status_value}"
        )
    if status_value != TaskStatus.STALLED.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task {task_id} is {status_value}; "
                "only stalled tasks can be resumed"
            ),
        )

    run_store = get_doc_run_store(repo, task_id)
    request_payload = run_store.load_request()
    checkpoint_payload = run_store.load_checkpoint()
    if not request_payload or not checkpoint_payload:
        raise HTTPException(
            status_code=404,
            detail=f"No local checkpoint found for task {task_id}; cannot resume",
        )

    background_task = asyncio.create_task(
        _generate_overview_background(
            task_id=task_id,
            repo=repo,
            language=str(request_payload.get("language", "en")),
            doc_depth=int(request_payload.get("doc_depth", 2)),
            mode=str(request_payload.get("mode", "detailed")),
            focus=str(request_payload.get("focus", "")) or None,
            model=str(request_payload.get("model", "")) or None,
            paper_id=str(request_payload.get("paper_id", "")) or None,
            resume=True,
        )
    )
    task_manager.register_task(task_id, background_task)

    logger.info(f"Resuming documentation generation: {task_id} for {repo}")
    return GenerateOverviewResponse(
        task_id=task_id,
        status="running",
        message="Documentation generation resumed",
    )


@router.get("/{repo}/generate/{task_id}/stream")
async def stream_generation_updates(
    repo: str,
    task_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Stream live updates about documentation generation via SSE.

    Returns a stream of Server-Sent Events with task status updates.

    Event types:
    - {"status": "pending|running|completed|failed|cancelled", "progress": 0-100, ...}
    """
    task_manager = get_task_manager()

    # Verify task exists
    task_state = await task_manager.get_task_status(task_id)
    if not task_state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    async def stream_events():
        """Stream task updates via SSE."""
        try:
            async for state in task_manager.stream_task_updates(task_id):
                yield f"data: {
                    json.dumps(
                        {
                            'task_id': state.task_id,
                            'status': state.status.value,
                            'progress': state.progress,
                            'step': state.step,
                            'message': state.status_message,
                            'error': state.error,
                            'trajectory': [
                                asdict(event) if not isinstance(event, dict) else event
                                for event in getattr(state, 'trajectory', [])
                            ],
                        }
                    )
                }\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{repo}/generate/{task_id}/cancel")
async def cancel_generation(
    repo: str,
    task_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Cancel a running documentation generation task.

    Returns:
        {"status": "cancelled", "task_id": "..."}
    """
    task_manager = get_task_manager()

    # Verify task exists
    task_state = await task_manager.get_task_status(task_id)
    if not task_state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # Try to cancel the task
    cancelled = await task_manager.cancel_task(task_id)

    if cancelled:
        logger.info(f"Task cancelled: {task_id} for {repo}")
        return {
            "status": "cancelled",
            "task_id": task_id,
            "message": "Task has been cancelled",
        }
    else:
        # Task may have already completed
        return {
            "status": "not_running",
            "task_id": task_id,
            "message": "Task is not running",
        }


@router.get("/{repo}")
async def get_overview_index(
    repo: str,
    version_id: str | None = None,
    user_id: str = Depends(get_current_user),
):
    """
    Get the _index.json file for documentation.

    Args:
        repo: Repository name
        version_id: Optional version ID. If not provided, returns the current version.

    Returns the navigation tree and metadata for the repository documentation.
    """
    logger.info(f"[get_overview_index] repo={repo}, version_id={version_id}")
    wiki_doc_path = get_wiki_doc_path()
    repo_path = wiki_doc_path / repo
    meta_path = repo_path / "_meta.json"

    # Determine which version to load
    target_version = version_id
    if not target_version and meta_path.exists():
        try:
            with open(meta_path) as f:
                meta_data = json.load(f)
            target_version = meta_data.get("current_version")
            if not target_version:
                versions = meta_data.get("versions", [])
                if versions:
                    target_version = versions[-1].get("version_id")
        except Exception as e:
            logger.warning(f"Failed to read meta: {e}")

    # Build index path
    if target_version:
        index_path = repo_path / "versions" / target_version / "_index.json"
    else:
        # Legacy fallback for repos without versioned structure
        index_path = repo_path / "_index.json"

    logger.info(
        f"[get_overview_index] target_version={target_version}, index_path={index_path}"
    )

    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Documentation not found for {repo}. Generate it first using POST /overview/{repo}/generate",
        )

    try:
        with open(index_path) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load index: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load index: {e}")


@router.get("/{repo}/doc/{path:path}")
async def get_overview_doc(
    repo: str,
    path: str,
    version_id: str | None = None,
    user_id: str = Depends(get_current_user),
):
    """
    Get a specific markdown document from documentation.

    Args:
        repo: Repository name
        path: Relative path to markdown file (e.g., "sections/001_xxx.md")
        version_id: Optional version ID to fetch a specific version (e.g., "20251201_143022_overview")

    Returns:
        Markdown content as plain text
    """
    logger.info(f"[get_overview_doc] repo={repo}, path={path}, version_id={version_id}")

    # Validate path to prevent directory traversal
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Ensure path ends with .md
    if not path.endswith(".md"):
        raise HTTPException(status_code=400, detail="Path must end with .md")

    wiki_doc_path = get_wiki_doc_path()
    repo_path = wiki_doc_path / repo

    # Determine document path based on version_id
    if version_id:
        # Look in specific version directory
        doc_path = repo_path / "versions" / version_id / path
    else:
        # Try to find current version from _meta.json
        meta_path = repo_path / "_meta.json"
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta_data = json.load(f)
                current_version = meta_data.get("current_version")
                if current_version:
                    doc_path = repo_path / "versions" / current_version / path
                else:
                    # Fallback to first version if available
                    versions = meta_data.get("versions", [])
                    if versions:
                        doc_path = (
                            repo_path / "versions" / versions[-1]["version_id"] / path
                        )
                    else:
                        doc_path = repo_path / path  # Legacy fallback
            except Exception:
                doc_path = repo_path / path  # Legacy fallback
        else:
            doc_path = repo_path / path  # Legacy fallback

    logger.info(f"[get_overview_doc] doc_path={doc_path}, exists={doc_path.exists()}")

    if not doc_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Document not found: {path}"
            + (f" (version: {version_id})" if version_id else ""),
        )

    try:
        with open(doc_path) as f:
            return PlainTextResponse(
                f.read(), media_type="text/markdown; charset=utf-8"
            )
    except Exception as e:
        logger.error(f"Failed to read document: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read document: {e}")


@router.get("/{repo}/version/{version_id}")
async def get_version_index(
    repo: str,
    version_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Get the _index.json file for a specific documentation version.

    Args:
        repo: Repository name
        version_id: Version ID (e.g., "20251201_143022_overview")

    Returns:
        The index data for the specified version
    """
    wiki_doc_path = get_wiki_doc_path()
    index_path = wiki_doc_path / repo / "versions" / version_id / "_index.json"

    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Version not found: {version_id}",
        )

    try:
        with open(index_path) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load version index: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to load version index: {e}"
        )


class SetCurrentVersionRequest(BaseModel):
    """Request model for setting current version."""

    version_id: str = Field(..., description="Version ID to set as current")


@router.put("/{repo}/version/current")
async def set_current_version(
    repo: str,
    request: SetCurrentVersionRequest,
    user_id: str = Depends(get_current_user),
):
    """
    Set the current (default) version for a repository's documentation.

    Updates the current_version field in _meta.json.

    Args:
        repo: Repository name
        request: Request containing the version_id to set as current

    Returns:
        {"success": True, "current_version": "..."}
    """
    wiki_doc_path = get_wiki_doc_path()
    repo_path = wiki_doc_path / repo
    meta_path = repo_path / "_meta.json"
    version_path = repo_path / "versions" / request.version_id

    # Verify version exists
    if not version_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Version not found: {request.version_id}",
        )

    # Load and update _meta.json
    if not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Meta file not found for repository: {repo}",
        )

    try:
        with open(meta_path) as f:
            meta_data = json.load(f)

        # Verify version is in the versions list
        version_ids = [v.get("version_id") for v in meta_data.get("versions", [])]
        if request.version_id not in version_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Version {request.version_id} not found in versions list",
            )

        # Update current version
        meta_data["current_version"] = request.version_id

        with open(meta_path, "w") as f:
            json.dump(meta_data, f, indent=2)

        logger.info(f"Set current version for {repo}: {request.version_id}")
        return {"success": True, "current_version": request.version_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set current version: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to set current version: {e}"
        )


@router.delete("/{repo}/version/{version_id}")
async def delete_version(
    repo: str,
    version_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Delete a specific documentation version.

    Removes the version directory and updates _meta.json.
    Cannot delete the last remaining version.

    Args:
        repo: Repository name
        version_id: Version ID to delete

    Returns:
        {"success": True, "deleted_version": "...", "new_current_version": "..."}
    """
    import shutil

    wiki_doc_path = get_wiki_doc_path()
    repo_path = wiki_doc_path / repo
    meta_path = repo_path / "_meta.json"
    version_path = repo_path / "versions" / version_id

    # Load _meta.json
    if not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Meta file not found for repository: {repo}",
        )

    try:
        with open(meta_path) as f:
            meta_data = json.load(f)

        versions = meta_data.get("versions", [])

        # Cannot delete the last version
        if len(versions) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last remaining version. Generate a new version first.",
            )

        # Find and remove the version from the list
        version_index = None
        for i, v in enumerate(versions):
            if v.get("version_id") == version_id:
                version_index = i
                break

        if version_index is None:
            raise HTTPException(
                status_code=404,
                detail=f"Version not found: {version_id}",
            )

        # Remove from versions list
        versions.pop(version_index)

        # Update current_version if we're deleting it
        new_current_version = meta_data.get("current_version")
        if new_current_version == version_id:
            # Set to the most recent remaining version
            # Versions are sorted by generated_at descending after removal
            versions.sort(key=lambda v: v.get("generated_at", ""), reverse=True)
            new_current_version = versions[0].get("version_id") if versions else None
            meta_data["current_version"] = new_current_version

        meta_data["versions"] = versions

        # Delete the version directory if it exists
        if version_path.exists():
            shutil.rmtree(version_path)
            logger.info(f"Deleted version directory: {version_path}")

        # Save updated _meta.json
        with open(meta_path, "w") as f:
            json.dump(meta_data, f, indent=2)

        logger.info(
            f"Deleted version {version_id} from {repo}, new current: {new_current_version}"
        )
        return {
            "success": True,
            "deleted_version": version_id,
            "new_current_version": new_current_version,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete version: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete version: {e}")
