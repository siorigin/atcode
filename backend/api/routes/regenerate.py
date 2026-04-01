# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from pathlib import Path

from api.dependencies import get_orchestrator_pool
from api.middleware.auth import get_current_user
from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from core.config import get_wiki_doc_dir
from fastapi import APIRouter, Depends, HTTPException
from graph.service import MemgraphIngestor
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


async def _execute_doc_regenerate_task(
    task_id: str,
    repo: str,
    section_id: str,
    version_id: str,
    feedback: str,
    model: str | None,
    preserve_structure: bool,
) -> None:
    """
    Execute section regeneration task.

    Args:
        task_id: Task ID for tracking
        repo: Repository name
        section_id: Section ID to regenerate
        version_id: Version ID of the documentation
        feedback: User feedback for regeneration
        model: Optional model ID to use
        preserve_structure: Whether to preserve heading structure
    """
    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=10,
            step="Initializing regeneration",
            status_message="Loading section data...",
        )

        # Get wiki_doc path
        wiki_doc_path = Path(get_wiki_doc_dir(repo))

        # Initialize orchestrator
        orchestrator_pool = get_orchestrator_pool()
        orchestrator = orchestrator_pool.get_orchestrator(repo)
        if not orchestrator:
            raise ValueError(f"No orchestrator found for repo: {repo}")

        await task_manager.update_task(
            task_id,
            progress=20,
            step="Loading messages",
            status_message="Loading previous generation context...",
        )

        # Get ingestor
        ingestor = MemgraphIngestor()

        await task_manager.update_task(
            task_id,
            progress=30,
            step="Regenerating content",
            status_message="Regenerating section with feedback...",
        )

        # Call regenerate_section
        result = await orchestrator.regenerate_section(
            repo_name=repo,
            version_id=version_id,
            section_id=section_id,
            feedback=feedback,
            wiki_doc_path=wiki_doc_path,
            ingestor=ingestor,
            preserve_structure=preserve_structure,
        )

        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            step="Completed",
            status_message="Section regenerated successfully",
            result=result,
        )

        logger.info(f"Section regeneration completed: {task_id}")

    except Exception as e:
        logger.error(
            f"Section regeneration failed: {task_id}, error: {e}", exc_info=True
        )
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            status_message=f"Regeneration failed: {str(e)}",
        )
        raise
    finally:
        task_manager.unregister_task(task_id)


async def _execute_operator_regenerate_task(
    task_id: str,
    repo: str,
    operator_name: str,
    feedback: str,
    model: str | None,
    reuse_exploration: bool,
) -> None:
    """
    Execute operator regeneration task.

    Args:
        task_id: Task ID for tracking
        repo: Repository name
        operator_name: Operator name to regenerate
        feedback: User feedback for regeneration
        model: Optional model ID to use
        reuse_exploration: Whether to reuse explored nodes
    """
    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=10,
            step="Initializing regeneration",
            status_message="Loading operator data...",
        )

        # Get wiki_doc path
        wiki_doc_path = Path(get_wiki_doc_dir(repo))
        operator_file = wiki_doc_path / "operators" / f"{operator_name}.json"

        # Load operator data
        with open(operator_file, encoding="utf-8") as f:
            operator_data = json.load(f)

        await task_manager.update_task(
            task_id,
            progress=20,
            step="Loading messages",
            status_message="Loading previous generation context...",
        )

        # Deserialize messages
        from langchain_core.messages import HumanMessage, messages_from_dict

        messages = messages_from_dict(operator_data.get("messages", []))

        # Add feedback
        feedback_prompt = f"""
User feedback for regeneration:
{feedback}

Please regenerate the documentation taking this feedback into account.
"""
        messages.append(HumanMessage(content=feedback_prompt))

        await task_manager.update_task(
            task_id,
            progress=30,
            step="Regenerating content",
            status_message="Regenerating operator documentation...",
        )

        # Initialize orchestrator
        orchestrator_pool = get_orchestrator_pool()
        orchestrator = orchestrator_pool.get_orchestrator(repo)
        if not orchestrator:
            raise ValueError(f"No orchestrator found for repo: {repo}")

        # Get ingestor
        ingestor = MemgraphIngestor()

        # Build initial state
        initial_state = {
            "messages": messages,
            "current_depth": 0,
            "scope_title": operator_data.get("title", operator_name),
            "scope_description": operator_data.get("original_query", ""),
            "explored_nodes": operator_data.get("explored_nodes", [])
            if reuse_exploration
            else [],
            "extraction_summaries": operator_data.get("extraction_summaries", []),
            "repo_name": repo,
            "current_step": "regenerate",
            "progress": 0,
        }

        # Run workflow
        workflow = orchestrator._build_doc_agent_workflow(ingestor, repo)
        app = workflow.compile()
        final_state = await app.ainvoke(initial_state)

        # Extract content
        from langchain_core.messages import AIMessage

        regenerated_messages = final_state.get("messages", [])
        content = ""
        for msg in reversed(regenerated_messages):
            if isinstance(msg, AIMessage) and msg.content:
                raw = msg.content
                if "<Doc>True</Doc>" in raw:
                    parts = raw.split("<Doc>True</Doc>", 1)
                    if len(parts) > 1:
                        content = parts[1].strip()
                        break
                elif len(raw) > 200:
                    content = raw
                    break

        # Update operator data
        from datetime import UTC, datetime

        from langchain_core.messages import ToolMessage, messages_to_dict

        operator_data["markdown"] = content
        operator_data["messages"] = messages_to_dict(regenerated_messages)
        operator_data["explored_nodes"] = final_state.get("explored_nodes", [])
        operator_data["extraction_summaries"] = final_state.get(
            "extraction_summaries", []
        )
        operator_data["regenerated_at"] = datetime.now(UTC).isoformat()
        operator_data["metadata"]["tool_call_count"] = sum(
            1 for msg in regenerated_messages if isinstance(msg, ToolMessage)
        )

        # Save updated operator data
        with open(operator_file, "w", encoding="utf-8") as f:
            json.dump(operator_data, f, ensure_ascii=False, indent=2)

        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            step="Completed",
            status_message="Operator regenerated successfully",
            result={
                "operator_name": operator_name,
                "content_length": len(content),
                "regenerated_at": operator_data["regenerated_at"],
            },
        )

        logger.info(f"Operator regeneration completed: {task_id}")

    except Exception as e:
        logger.error(
            f"Operator regeneration failed: {task_id}, error: {e}", exc_info=True
        )
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            status_message=f"Regeneration failed: {str(e)}",
        )
        raise
    finally:
        task_manager.unregister_task(task_id)


class SectionRegenerateRequest(BaseModel):
    """Section regeneration request model."""

    version_id: str = Field(
        ..., min_length=1, description="Version ID of the documentation"
    )
    feedback: str = Field(
        ..., min_length=1, description="User feedback for regeneration"
    )
    model: str | None = Field(
        None, description="Optional model ID to use for regeneration"
    )
    preserve_structure: bool = Field(
        True, description="Whether to preserve the same heading structure"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "version_id": "20260122_103000_overview",
                "feedback": "Please add more details about the caching mechanism",
                "preserve_structure": True,
            }
        }


class OperatorRegenerateRequest(BaseModel):
    """Operator documentation regeneration request model."""

    feedback: str = Field(
        ..., min_length=1, description="User feedback for regeneration"
    )
    model: str | None = Field(
        None, description="Optional model ID to use for regeneration"
    )
    reuse_exploration: bool = Field(
        True, description="Whether to reuse previously explored nodes"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "feedback": "Focus more on CUDA implementation details",
                "reuse_exploration": True,
            }
        }


class RegenerateResponse(BaseModel):
    """Regeneration response model."""

    task_id: str = Field(..., description="Task ID for tracking regeneration progress")
    status: str = Field(..., description="Initial task status")
    message: str = Field(..., description="Status message")


class SectionMessagesInfoResponse(BaseModel):
    """Section messages info response model."""

    section_id: str = Field(..., description="Section ID")
    messages_count: int = Field(..., description="Number of messages")
    explored_nodes_count: int = Field(..., description="Number of explored nodes")
    tool_call_count: int = Field(..., description="Number of tool calls")
    has_messages: bool = Field(..., description="Whether messages file exists")


@router.post(
    "/docs/{repo}/sections/{section_id}/regenerate",
    response_model=RegenerateResponse,
    summary="Regenerate a documentation section",
    description="Regenerate a specific documentation section with user feedback",
)
async def regenerate_section(
    repo: str,
    section_id: str,
    request: SectionRegenerateRequest,
    task_manager=Depends(get_task_manager),
    current_user: dict = Depends(get_current_user),
):
    """
    Regenerate a specific documentation section with user feedback.

    Args:
        repo: Repository name
        section_id: Section ID (e.g., "001_architecture")
        request: Regeneration request with feedback and options
        task_manager: Task manager dependency
        current_user: Current authenticated user

    Returns:
        Task information for tracking regeneration progress
    """
    logger.info(f"Regenerate section request: repo={repo}, section_id={section_id}")

    # Validate that the section exists
    wiki_doc_path = Path(get_wiki_doc_dir(repo))
    version_path = wiki_doc_path / "versions" / request.version_id
    messages_file = version_path / "sections" / f"{section_id}.messages.json"

    if not messages_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Messages file not found for section {section_id} in version {request.version_id}",
        )

    # Create regeneration task
    task_id = await task_manager.create_task(
        task_type=TaskType.DOC_REGENERATE.value,
        repo_name=repo,
        user_id=current_user.get("user_id"),
        initial_message=f"Enqueued section regeneration for {section_id}",
    )

    # Create and register background task
    background_task = asyncio.create_task(
        _execute_doc_regenerate_task(
            task_id=task_id,
            repo=repo,
            section_id=section_id,
            version_id=request.version_id,
            feedback=request.feedback,
            model=request.model,
            preserve_structure=request.preserve_structure,
        )
    )
    task_manager.register_task(task_id, background_task)

    logger.info(f"Created regeneration task {task_id} for section {section_id}")

    return RegenerateResponse(
        task_id=task_id, status="pending", message="Section regeneration enqueued"
    )


@router.post(
    "/docs/{repo}/operators/{operator_name}/regenerate",
    response_model=RegenerateResponse,
    summary="Regenerate operator documentation",
    description="Regenerate operator/function documentation with user feedback",
)
async def regenerate_operator(
    repo: str,
    operator_name: str,
    request: OperatorRegenerateRequest,
    task_manager=Depends(get_task_manager),
    current_user: dict = Depends(get_current_user),
):
    """
    Regenerate operator documentation with user feedback.

    Args:
        repo: Repository name
        operator_name: Operator/function name
        request: Regeneration request with feedback and options
        task_manager: Task manager dependency
        current_user: Current authenticated user

    Returns:
        Task information for tracking regeneration progress
    """
    logger.info(f"Regenerate operator request: repo={repo}, operator={operator_name}")

    # Validate that the operator documentation exists
    wiki_doc_path = Path(get_wiki_doc_dir(repo))
    operator_file = wiki_doc_path / "operators" / f"{operator_name}.json"

    if not operator_file.exists():
        raise HTTPException(
            status_code=404, detail=f"Operator documentation not found: {operator_name}"
        )

    # Load operator data to check for messages
    try:
        with open(operator_file, encoding="utf-8") as f:
            operator_data = json.load(f)

        if "messages" not in operator_data:
            raise HTTPException(
                status_code=400,
                detail="Operator documentation does not support regeneration (no messages history)",
            )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500, detail="Failed to parse operator documentation file"
        )

    # Create regeneration task
    task_id = await task_manager.create_task(
        task_type=TaskType.OPERATOR_REGENERATE.value,
        repo_name=repo,
        user_id=current_user.get("user_id"),
        initial_message=f"Enqueued operator regeneration for {operator_name}",
    )

    # Create and register background task
    background_task = asyncio.create_task(
        _execute_operator_regenerate_task(
            task_id=task_id,
            repo=repo,
            operator_name=operator_name,
            feedback=request.feedback,
            model=request.model,
            reuse_exploration=request.reuse_exploration,
        )
    )
    task_manager.register_task(task_id, background_task)

    logger.info(f"Created regeneration task {task_id} for operator {operator_name}")

    return RegenerateResponse(
        task_id=task_id, status="pending", message="Operator regeneration enqueued"
    )


@router.get(
    "/docs/{repo}/sections/{section_id}/messages",
    response_model=SectionMessagesInfoResponse,
    summary="Get section messages info",
    description="Get information about messages history for a section",
)
async def get_section_messages_info(
    repo: str,
    section_id: str,
    version_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get information about messages history for a section.

    Args:
        repo: Repository name
        section_id: Section ID (e.g., "001_architecture")
        version_id: Version ID of the documentation
        current_user: Current authenticated user

    Returns:
        Messages information including counts and availability
    """
    logger.info(
        f"Get messages info: repo={repo}, section_id={section_id}, version={version_id}"
    )

    wiki_doc_path = Path(get_wiki_doc_dir(repo))
    version_path = wiki_doc_path / "versions" / version_id
    messages_file = version_path / "sections" / f"{section_id}.messages.json"

    if not messages_file.exists():
        return SectionMessagesInfoResponse(
            section_id=section_id,
            messages_count=0,
            explored_nodes_count=0,
            tool_call_count=0,
            has_messages=False,
        )

    try:
        with open(messages_file, encoding="utf-8") as f:
            messages_data = json.load(f)

        return SectionMessagesInfoResponse(
            section_id=section_id,
            messages_count=len(messages_data.get("messages", [])),
            explored_nodes_count=len(messages_data.get("explored_nodes", [])),
            tool_call_count=messages_data.get("metadata", {}).get("tool_call_count", 0),
            has_messages=True,
        )
    except Exception as e:
        logger.error(f"Failed to read messages file: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to read messages file: {str(e)}"
        )
