# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Semantic code search using Memgraph native vector search.

This module provides semantic search functionality that finds code entities
by comparing embeddings stored in Memgraph's native vector indexes.
"""

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from .graph_query import QueryResult


def semantic_code_search(
    query: str,
    top_k: int = 5,
    repo_basename: str | None = None,
    ingestor: Any | None = None,
) -> list[dict[str, Any]]:
    """Search for functions/methods by natural language intent using semantic embeddings.

    This function performs semantic search using Memgraph's native vector search
    capability. It generates an embedding for the query and finds the most
    similar code entities in the knowledge graph.

    Args:
        query: Natural language description of desired functionality
        top_k: Number of results to return
        repo_basename: Repository basename for storage isolation (required)
        ingestor: MemgraphIngestor instance (required)

    Returns:
        List of dictionaries with function information:
        [
            {
                "qualified_name": str,
                "score": float,
                "type": str  # "Function", "Method", or "Class"
            }
        ]
    """
    if not repo_basename:
        logger.warning("repo_basename is required for semantic_code_search")
        return []

    if ingestor is None:
        logger.warning("ingestor is required for semantic_code_search")
        return []

    try:
        from graph.embedder import embed_code

        # Generate embedding for the query using API
        query_embedding = embed_code(query)

        # Perform Memgraph native vector search
        all_results: list[dict[str, Any]] = []

        # Search all relevant indexes
        index_type_map: list[tuple[str, str]] = [
            ("function_embedding_idx", "Function"),
            ("method_embedding_idx", "Method"),
            ("class_embedding_idx", "Class"),
        ]

        for index_name, node_type in index_type_map:
            try:
                hits = ingestor.vector_search(
                    index_name=index_name,
                    query_vector=query_embedding,
                    top_k=top_k,
                    project_name=repo_basename,
                )
                for hit in hits:
                    all_results.append(
                        {
                            "qualified_name": hit.get("qualified_name"),
                            "score": round(hit.get("similarity", 0), 3),
                            "type": node_type,
                        }
                    )
            except Exception as e:
                logger.debug(f"Search on {index_name} failed: {e}")

        # Sort by score and deduplicate
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        seen = set()
        unique_results = []
        for r in all_results:
            qn = r["qualified_name"]
            if qn and qn not in seen:
                seen.add(qn)
                unique_results.append(r)
            if len(unique_results) >= top_k:
                break

        if unique_results:
            logger.info(f"Found {len(unique_results)} semantic matches via Memgraph")
        return unique_results

    except Exception as e:
        logger.error(f"Semantic search failed for query '{query}': {e}")
        return []


class SemanticSearchInput(BaseModel):
    """Input schema for semantic_search_functions tool."""

    query: str = Field(
        description="Natural language description of the desired functionality"
    )
    top_k: int = Field(
        default=5, ge=1, le=20, description="Number of results to return (1-20)"
    )


def create_semantic_search_tool(
    repo_basename: str | None = None,
    ingestor: Any | None = None,
) -> BaseTool:
    """Factory function to create the semantic code search tool in LangChain format.

    Args:
        repo_basename: Repository basename for storage isolation
        ingestor: MemgraphIngestor instance for Memgraph-based search
    """

    async def semantic_search_functions(query: str, top_k: int = 5) -> QueryResult:
        """Search for functions/methods using natural language descriptions of their purpose.

        Use this tool when you need to find code that performs specific functionality
        based on intent rather than exact names. Perfect for questions like:
        - "Find error handling functions"
        - "Show me authentication-related code"
        - "Where is data validation implemented?"
        - "Find functions that handle file I/O"

        The query parameter should be a natural language description of the desired functionality.
        The top_k parameter controls the maximum number of results to return (default: 5).

        Returns a QueryResult with found functions and their semantic similarity scores.
        """
        logger.info(f"[Tool:SemanticSearch] Searching for: '{query}'")

        results = semantic_code_search(
            query, top_k, repo_basename=repo_basename, ingestor=ingestor
        )

        if not results:
            summary = (
                f"No semantic matches found for query: '{query}'. This could mean:\n"
                "1. No functions match this description\n"
                "2. Vector indexes have not been created yet\n"
                "3. No embeddings have been generated for the repository"
            )
            return QueryResult(
                success=False, results=[], count=0, summary=summary, query_used=query
            )

        # Create summary
        summary = f"Found {len(results)} semantic matches for '{query}':\n\n"
        formatted_results = []
        for i, result in enumerate(results, 1):
            type_str = f" [{result.get('type', 'Unknown')}]" if "type" in result else ""
            formatted_results.append(
                f"{i}. {result['qualified_name']}{type_str} (similarity: {result['score']})"
            )
        summary += "\n".join(formatted_results)
        summary += "\n\nTo view the source code of any function, use the get_code_snippet tool with the qualified_name above."

        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=summary,
            query_used=query,
        )

    return StructuredTool.from_function(
        coroutine=semantic_search_functions,
        name="semantic_search_functions",
        description="""Search functions by natural language intent (semantic similarity).

Slower than find_nodes. Best for abstract/intent queries like "error handling" or "data validation".
Use find_nodes first when you know the name or keyword.""",
        args_schema=SemanticSearchInput,
    )
