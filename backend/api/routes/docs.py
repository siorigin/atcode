# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from collections.abc import AsyncGenerator

from api.dependencies import get_orchestrator_pool
from api.middleware.auth import get_current_user
from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

router = APIRouter()


class DocGenerationRequest(BaseModel):
    """Documentation generation request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    operator: str = Field(
        ..., min_length=1, description="Operator/function name to document"
    )
    query_template: str | None = Field(
        None,
        description="Optional custom query template. Use {operator} as placeholder.",
    )
    model: str | None = Field(
        None,
        description="Optional model ID to use for generation. If not specified, uses default.",
    )

    @field_validator("operator")
    @classmethod
    def validate_operator_name(cls, v: str) -> str:
        """Validate operator name to avoid conflicts with overview system files."""
        reserved_names = ["_meta", "versions", "_index"]
        if v.startswith("_"):
            raise ValueError(
                "Operator name cannot start with '_' (reserved for system files)"
            )
        if v.lower() in reserved_names:
            raise ValueError(f"Operator name '{v}' is reserved and cannot be used")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "repo": "sglang",
                "operator": "reshape_and_cache_flash",
                "query_template": "查找{operator}，生成详细的中文文档。",
            }
        }


class BatchDocGenerationRequest(BaseModel):
    """Batch documentation generation request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    operators: list[str] = Field(
        ..., min_items=1, description="List of operators to document"
    )
    query_template: str | None = Field(
        None,
        description="Optional custom query template. Use {operator} as placeholder.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "repo": "sglang",
                "operators": ["reshape_and_cache_flash", "rotary_embedding"],
                "query_template": "查找{operator}，生成详细的中文文档。",
            }
        }


class DocGenerationResponse(BaseModel):
    """Documentation generation response model."""

    success: bool = Field(..., description="Whether generation succeeded")
    repo: str = Field(..., description="Repository name")
    operator: str = Field(..., description="Operator name")
    file_path: str | None = Field(None, description="Path to saved documentation")
    doc_id: str | None = Field(None, description="Documentation ID")
    url: str | None = Field(None, description="Documentation URL")
    error: str | None = Field(None, description="Error message if failed")


async def _generate_documentation_stream(
    repo_name: str,
    operator_name: str,
    query_template: str | None,
    pool,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generate documentation for a single operator with streaming progress.

    Args:
        repo_name: Repository name
        operator_name: Operator to document
        query_template: Custom query template (optional)
        pool: Orchestrator pool

    Yields:
        SSE formatted event strings
    """
    from agent.orchestrators.doc import (
        initialize_doc_agent,
        save_documentation_with_operator_name,
    )
    from core.prompts import DocMode as PromptDocMode

    try:
        # Send initial status
        yield f"data: {json.dumps({'type': 'status', 'operator': operator_name, 'content': 'Initializing...'})}\n\n"

        # Get repo path
        repo_path = pool.get_repo_path(repo_name)
        if not repo_path.exists():
            yield f"data: {json.dumps({'type': 'error', 'operator': operator_name, 'content': f'Repository path not found: {repo_path}'})}\n\n"
            return

        # Get ingestor from pool
        ingestor = pool.get_ingestor()
        if not ingestor:
            yield f"data: {json.dumps({'type': 'error', 'operator': operator_name, 'content': 'Failed to get database connection'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'status', 'operator': operator_name, 'content': 'Creating orchestrator...'})}\n\n"

        # Create doc agent for research mode
        rag_agent = initialize_doc_agent(
            str(repo_path), ingestor, doc_mode=PromptDocMode.RESEARCH, model=model
        )

        yield f"data: {json.dumps({'type': 'status', 'operator': operator_name, 'content': 'Generating documentation...'})}\n\n"

        # Build query
        if query_template:
            query = query_template.format(operator=operator_name)
        else:
            query = f"查找{operator_name}，生成详细的中文文档。注意其计算逻辑和底层，比如说triton，C,CUDA，pytorch等相关的实现细节。可以重点关注这部分，不需要生成完整的代码，因为我会结构化解析出来"

        # Run documentation generation
        response = await rag_agent.run(query, message_history=[])

        # Check if documentation was generated
        if hasattr(response, "documentation_data") and response.documentation_data:
            doc_data = response.documentation_data

            # Save with operator-based naming
            new_file_path = save_documentation_with_operator_name(
                doc_data, repo_name, operator_name
            )

            yield f"data: {json.dumps({'type': 'complete', 'operator': operator_name, 'content': 'Documentation generated successfully', 'metadata': {'file_path': new_file_path, 'doc_id': doc_data.get('id'), 'url': doc_data.get('url')}})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'operator': operator_name, 'content': 'No documentation data returned'})}\n\n"

    except asyncio.CancelledError:
        logger.info(f"Documentation generation cancelled for {operator_name}")
        yield f"data: {json.dumps({'type': 'error', 'operator': operator_name, 'content': 'Generation cancelled'})}\n\n"

    except Exception as e:
        logger.error(f"Documentation generation error for {operator_name}: {e}")
        yield f"data: {json.dumps({'type': 'error', 'operator': operator_name, 'content': str(e)})}\n\n"


@router.post(
    "/generate/stream",
    summary="Generate Documentation (Streaming)",
    description="Generate documentation for a single operator with streaming progress updates.",
    response_class=StreamingResponse,
)
async def generate_doc_stream(
    request: DocGenerationRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
):
    """
    Generate documentation for a single operator with streaming SSE progress.

    Args:
        request: Documentation generation request
        user_id: Current user ID (from auth)
        pool: Orchestrator pool

    Returns:
        StreamingResponse with SSE events
    """
    logger.info(
        f"Doc generation request: user={user_id}, repo={request.repo}, operator={request.operator}"
    )

    return StreamingResponse(
        _generate_documentation_stream(
            request.repo, request.operator, request.query_template, pool, request.model
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/generate",
    summary="Generate Documentation",
    description="Generate documentation for a single operator (non-streaming).",
    response_model=DocGenerationResponse,
)
async def generate_doc(
    request: DocGenerationRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
) -> DocGenerationResponse:
    """
    Generate documentation for a single operator (non-streaming).

    Args:
        request: Documentation generation request
        user_id: Current user ID
        pool: Orchestrator pool

    Returns:
        Documentation generation response
    """
    from agent.orchestrators.doc import (
        initialize_doc_agent,
        save_documentation_with_operator_name,
    )
    from core.prompts import DocMode as PromptDocMode

    logger.info(
        f"Doc generation request: user={user_id}, repo={request.repo}, operator={request.operator}"
    )

    try:
        # Get repo path
        repo_path = pool.get_repo_path(request.repo)
        if not repo_path.exists():
            return DocGenerationResponse(
                success=False,
                repo=request.repo,
                operator=request.operator,
                error=f"Repository path not found: {repo_path}",
            )

        # Get ingestor from pool
        ingestor = pool.get_ingestor()
        if not ingestor:
            return DocGenerationResponse(
                success=False,
                repo=request.repo,
                operator=request.operator,
                error="Failed to get database connection",
            )

        # Create doc agent for research mode
        rag_agent = initialize_doc_agent(
            str(repo_path),
            ingestor,
            doc_mode=PromptDocMode.RESEARCH,
            model=request.model,
        )

        # Build query
        # Support both templated queries (with {operator} placeholder) and direct research descriptions
        if request.query_template:
            if "{operator}" in request.query_template:
                # Template with placeholder - substitute operator name
                query = request.query_template.format(operator=request.operator)
            else:
                # Direct research description - use as-is
                query = request.query_template
        else:
            # Default query for operator documentation
            query = f"查找{request.operator}，生成详细的中文文档。注意其计算逻辑和底层，比如说triton，C,CUDA，pytorch等相关的实现细节。可以重点关注这部分，不需要生成完整的代码，因为我会结构化解析出来"

        # Run documentation generation
        response = await rag_agent.run(query, message_history=[])

        # Check if documentation was generated
        if hasattr(response, "documentation_data") and response.documentation_data:
            doc_data = response.documentation_data

            # Save with operator-based naming
            new_file_path = save_documentation_with_operator_name(
                doc_data, request.repo, request.operator
            )

            return DocGenerationResponse(
                success=True,
                repo=request.repo,
                operator=request.operator,
                file_path=new_file_path,
                doc_id=doc_data.get("id"),
                url=doc_data.get("url"),
            )
        else:
            return DocGenerationResponse(
                success=False,
                repo=request.repo,
                operator=request.operator,
                error="No documentation data returned",
            )

    except Exception as e:
        logger.error(f"Documentation generation error: {e}")
        return DocGenerationResponse(
            success=False, repo=request.repo, operator=request.operator, error=str(e)
        )


# ============== Async Single Doc Generation with Task Tracking ==============


class AsyncDocGenRequest(BaseModel):
    """Async documentation generation request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    operator: str = Field(
        ..., min_length=1, description="Operator/topic name to document"
    )
    query_template: str | None = Field(
        None, description="Custom query template. Use {operator} as placeholder."
    )
    model: str | None = Field(
        None, description="Optional model ID to use for generation."
    )

    @field_validator("operator")
    @classmethod
    def validate_operator_name(cls, v: str) -> str:
        """Validate operator name to avoid conflicts with overview system files."""
        reserved_names = ["_meta", "versions", "_index"]
        if v.startswith("_"):
            raise ValueError(
                "Operator name cannot start with '_' (reserved for system files)"
            )
        if v.lower() in reserved_names:
            raise ValueError(f"Operator name '{v}' is reserved and cannot be used")
        return v


class AsyncDocGenResponse(BaseModel):
    """Response for async documentation generation."""

    success: bool = Field(..., description="Whether request was accepted")
    task_id: str = Field(..., description="Task ID for tracking progress")
    repo_name: str = Field(..., description="Repository name")
    operator: str = Field(..., description="Operator/topic being documented")
    message: str = Field("", description="Status message")


async def _process_doc_gen_task(
    task_id: str,
    repo_name: str,
    operator_name: str,
    query_template: str | None,
    model: str | None,
    pool,
):
    """Background task to generate documentation for a single operator/topic."""
    from agent.orchestrators.doc import (
        initialize_doc_agent,
        save_documentation_with_operator_name,
    )
    from core.prompts import DocMode as PromptDocMode

    task_manager = get_task_manager()

    try:
        model_info = f" with model {model}" if model else ""
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=5,
            step="initializing",
            status_message=f"{operator_name}: Starting documentation generation{model_info}...",
            details={
                "repo_name": repo_name,
                "operator_name": operator_name,
                "query_template_provided": bool(query_template),
                "model": model or "",
            },
        )

        # Get repo path and ingestor
        repo_path = pool.get_repo_path(repo_name)
        ingestor = pool.get_ingestor()

        if not repo_path.exists():
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error=f"Repository path not found: {repo_path}",
            )
            return

        if not ingestor:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error="Failed to get database connection",
            )
            return

        await task_manager.update_task(
            task_id,
            progress=10,
            step="creating_agent",
            status_message=f"{operator_name}: Creating documentation agent...",
            details={"repo_name": repo_name, "operator_name": operator_name},
        )

        # Create doc agent for research mode with optional model override
        rag_agent = initialize_doc_agent(
            str(repo_path),
            ingestor,
            doc_mode=PromptDocMode.RESEARCH,
            model=model,
        )

        await task_manager.update_task(
            task_id,
            progress=15,
            step="generating",
            status_message=f"{operator_name}: Generating documentation...",
            details={"repo_name": repo_name, "operator_name": operator_name},
        )

        # Build query
        if query_template:
            if "{operator}" in query_template:
                query = query_template.format(operator=operator_name)
            else:
                query = query_template
        else:
            query = f"查找{operator_name}，生成详细的中文文档。注意其计算逻辑和底层，比如说triton，C,CUDA，pytorch等相关的实现细节。可以重点关注这部分，不需要生成完整的代码，因为我会结构化解析出来"

        # Run documentation generation
        response = await rag_agent.run(query, message_history=[])

        if hasattr(response, "documentation_data") and response.documentation_data:
            doc_data = response.documentation_data

            await task_manager.update_task(
                task_id,
                progress=95,
                step="saving",
                status_message=f"{operator_name}: Saving documentation...",
                details={"repo_name": repo_name, "operator_name": operator_name},
            )

            # Save with operator-based naming
            file_path = save_documentation_with_operator_name(
                doc_data, repo_name, operator_name
            )

            await task_manager.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                step="complete",
                status_message=f"{operator_name}: Documentation generated successfully",
                result={
                    "file_path": file_path,
                    "doc_id": doc_data.get("id"),
                    "url": doc_data.get("url"),
                },
                details={
                    "repo_name": repo_name,
                    "operator_name": operator_name,
                    "file_path": file_path,
                    "doc_id": doc_data.get("id"),
                },
            )
            logger.info(f"Documentation generated for {operator_name}: {file_path}")
        else:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error="No documentation data returned",
            )

    except asyncio.CancelledError:
        logger.info(f"Documentation generation task cancelled: {task_id}")
        await task_manager.update_task(
            task_id,
            status=TaskStatus.CANCELLED,
            error="Task was cancelled by user",
        )
        raise

    except Exception as e:
        logger.error(f"Documentation generation failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
        )

    finally:
        # Always unregister the task when done
        task_manager.unregister_task(task_id)


@router.post(
    "/generate/async",
    response_model=AsyncDocGenResponse,
    summary="Async Generate Documentation",
    description="Start async documentation generation with progress tracking. Returns immediately with task_id.",
)
async def generate_doc_async(
    request: AsyncDocGenRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
) -> AsyncDocGenResponse:
    """
    Start async documentation generation for a single operator/topic.

    Returns immediately with task_id for progress tracking.
    Use /api/tasks/{task_id} to monitor progress.
    """
    logger.info(
        f"Async doc generation request: user={user_id}, repo={request.repo}, operator={request.operator}"
    )

    # Validate repository exists
    repo_path = pool.get_repo_path(request.repo)
    if not repo_path.exists():
        return AsyncDocGenResponse(
            success=False,
            task_id="",
            repo_name=request.repo,
            operator=request.operator,
            message=f"Repository not found: {request.repo}",
        )

    # Create task
    task_manager = get_task_manager()
    task_id = await task_manager.create_task(
        task_type=TaskType.DOC_GEN.value,
        repo_name=request.repo,
        user_id=user_id,
        initial_message=f"{request.operator}: Queued for documentation generation",
    )

    # Start background processing and register for cancellation support
    background_task = asyncio.create_task(
        _process_doc_gen_task(
            task_id=task_id,
            repo_name=request.repo,
            operator_name=request.operator,
            query_template=request.query_template,
            model=request.model,
            pool=pool,
        )
    )
    # Register the task so it can be cancelled
    task_manager.register_task(task_id, background_task)

    logger.info(
        f"Started async doc generation: {task_id} for {request.repo}/{request.operator}"
    )

    return AsyncDocGenResponse(
        success=True,
        task_id=task_id,
        repo_name=request.repo,
        operator=request.operator,
        message=f"Started documentation generation for {request.operator}. Track with task ID: {task_id}",
    )


@router.post(
    "/generate/batch",
    summary="Batch Generate Documentation",
    description="Generate documentation for multiple operators in parallel.",
    response_model=list[DocGenerationResponse],
)
async def generate_docs_batch(
    request: BatchDocGenerationRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
) -> list[DocGenerationResponse]:
    """
    Generate documentation for multiple operators in parallel.

    Args:
        request: Batch documentation generation request
        user_id: Current user ID
        pool: Orchestrator pool

    Returns:
        List of documentation generation responses
    """
    from agent.orchestrators.doc import (
        initialize_doc_agent,
        save_documentation_with_operator_name,
    )
    from core.prompts import DocMode as PromptDocMode

    logger.info(
        f"Batch doc generation: user={user_id}, repo={request.repo}, operators={request.operators}"
    )

    # Get repo path and ingestor once
    repo_path = pool.get_repo_path(request.repo)
    ingestor = pool.get_ingestor()

    if not repo_path.exists():
        return [
            DocGenerationResponse(
                success=False,
                repo=request.repo,
                operator=op,
                error=f"Repository path not found: {repo_path}",
            )
            for op in request.operators
        ]

    if not ingestor:
        return [
            DocGenerationResponse(
                success=False,
                repo=request.repo,
                operator=op,
                error="Failed to get database connection",
            )
            for op in request.operators
        ]

    async def generate_single(operator_name: str) -> DocGenerationResponse:
        """Generate documentation for a single operator."""
        try:
            # Create a new doc agent for each operator (allows parallel execution)
            rag_agent = initialize_doc_agent(
                str(repo_path), ingestor, doc_mode=PromptDocMode.RESEARCH
            )

            # Build query
            if request.query_template:
                query = request.query_template.format(operator=operator_name)
            else:
                query = f"查找{operator_name}，生成详细的中文文档。注意其计算逻辑和底层，比如说triton，C,CUDA，pytorch等相关的实现细节。可以重点关注这部分，不需要生成完整的代码，因为我会结构化解析出来"

            # Run documentation generation
            response = await rag_agent.run(query, message_history=[])

            if hasattr(response, "documentation_data") and response.documentation_data:
                doc_data = response.documentation_data
                new_file_path = save_documentation_with_operator_name(
                    doc_data, request.repo, operator_name
                )

                return DocGenerationResponse(
                    success=True,
                    repo=request.repo,
                    operator=operator_name,
                    file_path=new_file_path,
                    doc_id=doc_data.get("id"),
                    url=doc_data.get("url"),
                )
            else:
                return DocGenerationResponse(
                    success=False,
                    repo=request.repo,
                    operator=operator_name,
                    error="No documentation data returned",
                )

        except Exception as e:
            logger.error(f"Failed to generate docs for {operator_name}: {e}")
            return DocGenerationResponse(
                success=False, repo=request.repo, operator=operator_name, error=str(e)
            )

    # Run all operators in parallel
    tasks = [generate_single(op) for op in request.operators]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    return results


@router.get(
    "/operators/{repo}",
    summary="List Operators",
    description="List all documented operators for a repository.",
)
async def list_operators(
    repo: str,
    _user_id: str = Depends(get_current_user),  # Auth required but user_id not used
):
    """
    List all documented operators for a repository.

    Args:
        repo: Repository name
        _user_id: Current user ID (auth required)

    Returns:
        List of operators with their documentation status
    """
    from core.config import get_wiki_doc_dir

    # Get wiki_doc directory using centralized config
    wiki_doc_path = get_wiki_doc_dir(repo)

    operators = []

    if wiki_doc_path.exists():
        # List all JSON files in the directory
        for doc_file in wiki_doc_path.glob("*.json"):
            operator_name = doc_file.stem

            # Get file modification time
            stat = doc_file.stat()

            operators.append(
                {
                    "name": operator_name,
                    "file_path": str(doc_file),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "has_documentation": True,
                }
            )

    return {"repo": repo, "operators": operators, "total": len(operators)}


# ============== Async Batch Regeneration with Task Tracking ==============


class AsyncBatchRegenRequest(BaseModel):
    """Async batch regeneration request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    operators: list[str] | None = Field(
        None, description="Specific operators to regenerate (if None, regenerates all)"
    )
    query_template: str | None = Field(None, description="Custom query template")


class AsyncBatchRegenResponse(BaseModel):
    """Response for async batch regeneration."""

    success: bool = Field(..., description="Whether request was accepted")
    task_id: str = Field(..., description="Task ID for tracking progress")
    repo_name: str = Field(..., description="Repository name")
    operator_count: int = Field(0, description="Number of operators to regenerate")
    message: str = Field("", description="Status message")


async def _process_batch_regen_task(
    task_id: str, repo_name: str, operators: list[str], query_template: str | None, pool
):
    """Background task to regenerate documentation for multiple operators."""
    from agent.orchestrators.doc import (
        initialize_doc_agent,
        save_documentation_with_operator_name,
    )
    from core.prompts import DocMode as PromptDocMode

    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=5,
            step="initializing",
            status_message=f"Starting regeneration of {len(operators)} operators...",
        )

        # Get repo path and ingestor
        repo_path = pool.get_repo_path(repo_name)
        ingestor = pool.get_ingestor()

        if not repo_path.exists():
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error=f"Repository path not found: {repo_path}",
            )
            return

        if not ingestor:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error="Failed to get database connection",
            )
            return

        success_count = 0
        fail_count = 0
        total = len(operators)

        for i, operator_name in enumerate(operators):
            try:
                # Update progress
                progress = 10 + int((i / total) * 85)  # 10-95%
                await task_manager.update_task(
                    task_id,
                    progress=progress,
                    step=f"regenerating_{operator_name}",
                    status_message=f"Regenerating {operator_name} ({i + 1}/{total})...",
                )

                # Create doc agent for this operator
                rag_agent = initialize_doc_agent(
                    str(repo_path), ingestor, doc_mode=PromptDocMode.RESEARCH
                )

                # Build query
                if query_template:
                    query = query_template.format(operator=operator_name)
                else:
                    query = f"查找{operator_name}，生成详细的中文文档。注意其计算逻辑和底层，比如说triton，C,CUDA，pytorch等相关的实现细节。可以重点关注这部分，不需要生成完整的代码，因为我会结构化解析出来"

                # Run documentation generation
                response = await rag_agent.run(query, message_history=[])

                if (
                    hasattr(response, "documentation_data")
                    and response.documentation_data
                ):
                    doc_data = response.documentation_data
                    save_documentation_with_operator_name(
                        doc_data, repo_name, operator_name
                    )
                    success_count += 1
                    logger.info(f"Regenerated documentation for {operator_name}")
                else:
                    fail_count += 1
                    logger.warning(
                        f"No documentation data returned for {operator_name}"
                    )

            except asyncio.CancelledError:
                raise  # Re-raise cancellation
            except Exception as e:
                fail_count += 1
                logger.error(f"Failed to regenerate docs for {operator_name}: {e}")

        # Complete
        if fail_count == 0:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                step="complete",
                status_message=f"Successfully regenerated {success_count}/{total} operators",
                result={
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "total": total,
                },
            )
        else:
            await task_manager.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                step="complete_with_errors",
                status_message=f"Completed with errors: {success_count}/{total} succeeded, {fail_count} failed",
                result={
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "total": total,
                },
            )

        logger.info(
            f"Batch regeneration completed for {repo_name}: {success_count}/{total}"
        )

    except asyncio.CancelledError:
        logger.info(f"Batch regeneration task cancelled: {task_id}")
        await task_manager.update_task(
            task_id,
            status=TaskStatus.CANCELLED,
            error="Task was cancelled by user",
        )
        raise

    except Exception as e:
        logger.error(f"Batch regeneration failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
        )

    finally:
        # Always unregister the task when done
        task_manager.unregister_task(task_id)


@router.post(
    "/regenerate/async",
    response_model=AsyncBatchRegenResponse,
    summary="Async Batch Regenerate Documentation",
    description="Start async batch documentation regeneration with progress tracking.",
)
async def regenerate_docs_async(
    request: AsyncBatchRegenRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
) -> AsyncBatchRegenResponse:
    """
    Start async batch documentation regeneration.

    Returns immediately with task_id for progress tracking.
    Use /api/tasks/{task_id} to monitor progress.
    """
    from core.config import get_wiki_doc_dir

    # Get wiki_doc directory using centralized config
    wiki_doc_path = get_wiki_doc_dir(request.repo)

    # Determine operators to regenerate
    if request.operators:
        operators = request.operators
    else:
        # Get all existing operators from wiki_doc
        if not wiki_doc_path.exists():
            return AsyncBatchRegenResponse(
                success=False,
                task_id="",
                repo_name=request.repo,
                operator_count=0,
                message=f"No documentation found for repository: {request.repo}",
            )

        operators = [
            f.stem for f in wiki_doc_path.glob("*.json") if not f.name.startswith("_")
        ]

    if not operators:
        return AsyncBatchRegenResponse(
            success=False,
            task_id="",
            repo_name=request.repo,
            operator_count=0,
            message="No operators found to regenerate",
        )

    # Create task
    task_manager = get_task_manager()
    task_id = await task_manager.create_task(
        task_type=TaskType.DOC_GEN.value,
        repo_name=request.repo,
        user_id=user_id,
        initial_message=f"Queued regeneration of {len(operators)} operators",
    )

    # Start background processing and register for cancellation support
    background_task = asyncio.create_task(
        _process_batch_regen_task(
            task_id=task_id,
            repo_name=request.repo,
            operators=operators,
            query_template=request.query_template,
            pool=pool,
        )
    )
    # Register the task so it can be cancelled
    task_manager.register_task(task_id, background_task)

    logger.info(
        f"Started async batch regeneration: {task_id} for {request.repo} ({len(operators)} operators)"
    )

    return AsyncBatchRegenResponse(
        success=True,
        task_id=task_id,
        repo_name=request.repo,
        operator_count=len(operators),
        message=f"Started regeneration of {len(operators)} operators. Track with task ID: {task_id}",
    )
