# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Chat workflow graph definition.

Extracted from chat.py for readability. Contains ChatState TypedDict and
the ``create_chat_workflow`` factory that builds the LangGraph state machine.
"""

import operator
import uuid
from pathlib import Path
from typing import Annotated, Any

from core.config import settings
from core.prompts import DEFAULT_MAX_TOOL_CALLS, generate_chat_prompt
from graph.service import MemgraphIngestor
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.messages.ai import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from loguru import logger
from typing_extensions import TypedDict

from .context import (
    ContextExtractionConfig,
    create_extract_context_node,
    should_trigger_extraction,
)
from .shared import (
    extract_qualified_names,
    get_recent_tool_messages,
    greedy_split_tool_names,
    invoke_with_retry,
    last_value,
    max_value,
    parse_tool_result,
    process_nodes_to_explored,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class ChatState(TypedDict):
    """State for the chat orchestrator workflow."""

    messages: Annotated[list[BaseMessage], add_messages]
    explored_nodes: Annotated[list[dict[str, Any]], operator.add]
    tool_call_count: int
    max_tool_calls: int
    turn_tool_calls: int
    current_repo_name: str
    is_global: bool  # True when running in __global__ mode (no specific repo)
    is_papers: bool  # True when running in __papers__ mode (paper research)
    # Context extraction fields
    extraction_round: Annotated[int, max_value]
    extraction_summaries: Annotated[list[str], operator.add]
    need_extraction: Annotated[bool, last_value]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _greedy_split_tool_names(
    concatenated: str, valid_names: set[str]
) -> list[str] | None:
    """Delegate to shared implementation."""
    return greedy_split_tool_names(concatenated, valid_names)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_chat_workflow(
    llm,
    all_tools: list,
    repo_path: Path,
    ingestor: MemgraphIngestor,
    is_papers: bool = False,
) -> Any:
    """Build and compile the chat LangGraph workflow.

    Args:
        llm: The LLM instance used by the agent node.
        all_tools: List of LangChain BaseTool objects.
        repo_path: Repository root path.
        ingestor: Memgraph ingestor for node processing.
        is_papers: Whether this is a paper chat session (uses lower extraction threshold).

    Returns:
        A compiled LangGraph ``StateGraph``.
    """
    captured_llm = llm
    captured_tools = all_tools

    # Build valid tool name set for concatenation detection
    valid_tool_names: set[str] = {t.name for t in all_tools}

    def _split_concatenated_tool_calls(response: AIMessage) -> AIMessage:
        """Fix tool calls whose names were concatenated by LangChain's merge_lists().

        When the upstream API (e.g. DMX) returns parallel tool-call chunks without
        proper ``index`` fields, LangChain merges them into a single tool-call with
        concatenated name, id, and merged args dict.  This function detects such
        cases and splits them back into individual tool calls.
        """
        if not response.tool_calls:
            return response

        fixed_calls: list[dict[str, Any]] = []
        changed = False

        for tc in response.tool_calls:
            name = tc["name"]
            if name in valid_tool_names:
                fixed_calls.append(tc)
                continue

            # Try to split the concatenated name into valid tool names
            # e.g. "get_paper_docget_children" → ["get_paper_doc", "get_children"]
            split_names = _greedy_split_tool_names(name, valid_tool_names)
            if not split_names:
                # Can't split — keep as-is and let ToolNode report the error
                logger.warning(f"Unrecognised tool name '{name}', cannot split")
                fixed_calls.append(tc)
                continue

            changed = True
            merged_args = tc.get("args", {})
            logger.info(
                f"Splitting concatenated tool call '{name}' → {split_names}"
            )

            # Distribute args to each sub-call by matching parameter names
            tool_schemas: dict[str, set[str]] = {}
            for t in captured_tools:
                if t.name in split_names:
                    schema = t.args_schema.schema() if hasattr(t, "args_schema") and t.args_schema else {}
                    props = set(schema.get("properties", {}).keys())
                    tool_schemas[t.name] = props

            for sub_name in split_names:
                expected_params = tool_schemas.get(sub_name, set())
                sub_args = {k: v for k, v in merged_args.items() if k in expected_params}
                fixed_calls.append({
                    "name": sub_name,
                    "args": sub_args,
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "tool_call",
                })

        if not changed:
            return response

        # Rebuild the AIMessage with corrected tool_calls
        new_response = AIMessage(
            content=response.content,
            tool_calls=fixed_calls,
            response_metadata=response.response_metadata,
            id=response.id,
        )
        return new_response

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    async def agent_node(state: ChatState) -> dict[str, Any]:
        """Agent node that processes messages with tool call budget."""
        messages = state["messages"]
        turn_tool_calls = state.get("turn_tool_calls", 0)
        max_tool_calls = state.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
        current_repo_name = state.get("current_repo_name", "unknown")

        # Rebuild system message every invocation so budget/context stays current
        explored_nodes = state.get("explored_nodes", [])
        explored_qns = (
            [n.get("qualified_name", "Unknown") for n in explored_nodes[-15:]]
            if explored_nodes
            else None
        )

        is_multi_turn = len(messages) > 1

        system_prompt = generate_chat_prompt(
            max_tool_calls=max_tool_calls,
            repo_name=current_repo_name,
            turn_tool_calls=turn_tool_calls,
            explored_nodes=explored_qns,
            is_multi_turn=is_multi_turn,
            is_global=state.get("is_global", False),
            is_papers=state.get("is_papers", False),
        )

        # Remove any existing SystemMessage and prepend the fresh one
        messages = [m for m in messages if not isinstance(m, SystemMessage)]
        messages = [SystemMessage(content=system_prompt)] + messages

        # Budget exhausted → force plain response
        if turn_tool_calls >= max_tool_calls:
            logger.info(
                f"Turn tool call budget exhausted ({turn_tool_calls}/{max_tool_calls}), forcing response"
            )
            response = await invoke_with_retry(
                captured_llm, messages, label="chat_agent(budget_exhausted)",
                config=settings.active_llm_config,
            )
            return {"messages": [response]}

        # Normal invocation with tools
        logger.debug(
            f"Agent invoking LLM (turn tool calls: {turn_tool_calls}/{max_tool_calls})"
        )
        llm_with_tools = captured_llm.bind_tools(captured_tools)
        response = await invoke_with_retry(
            llm_with_tools, messages, label="chat_agent",
            config=settings.active_llm_config,
            tools=captured_tools,
        )
        response = _split_concatenated_tool_calls(response)
        return {"messages": [response]}

    def should_continue_from_agent(state: ChatState) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        turn_tool_calls = state.get("turn_tool_calls", 0)
        max_tool_calls = state.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)

        if turn_tool_calls >= max_tool_calls:
            return END
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    async def update_state_with_tool_results(state: ChatState) -> dict[str, Any]:
        messages = state["messages"]
        tool_messages = get_recent_tool_messages(messages)

        existing_qns = extract_qualified_names(state.get("explored_nodes", []))
        new_explored_nodes = []

        for message in tool_messages:
            tool_result = parse_tool_result(message.content)
            if not tool_result:
                continue
            if tool_result.get("results"):
                explored_batch = process_nodes_to_explored(
                    tool_result["results"],
                    tool_result.get("tool_name", ""),
                    ingestor,
                    repo_path,
                )
                for node in explored_batch:
                    qn = node.get("qualified_name")
                    if qn and qn not in existing_qns:
                        new_explored_nodes.append(node)
                        existing_qns.add(qn)

        turn_tool_calls = state.get("turn_tool_calls", 0) + len(tool_messages)
        tool_call_count = state.get("tool_call_count", 0) + len(tool_messages)

        extraction_round = state.get("extraction_round", 0)
        _is_papers = state.get("is_papers", False)
        if _is_papers:
            config = ContextExtractionConfig(
                tool_call_threshold=12,
                keep_recent_messages=6,
                messages_per_round=8,
                auto_keep_token_threshold=200,
            )
        else:
            config = ContextExtractionConfig(
                tool_call_threshold=20,
                keep_recent_messages=8,
                messages_per_round=10,
                auto_keep_token_threshold=300,
            )
        need_extraction = should_trigger_extraction(
            tool_call_count, extraction_round, config
        )

        updates: dict[str, Any] = {
            "turn_tool_calls": turn_tool_calls,
            "tool_call_count": tool_call_count,
            "need_extraction": need_extraction,
        }
        if new_explored_nodes:
            updates["explored_nodes"] = new_explored_nodes

        logger.info(
            f"Processed {len(tool_messages)} tool calls, found {len(new_explored_nodes)} new nodes "
            f"(turn: {turn_tool_calls}, total: {tool_call_count})"
        )
        return updates

    def should_continue_from_tools(state: ChatState) -> str:
        if state.get("need_extraction", False):
            return "extract_context"
        return "agent"

    # ------------------------------------------------------------------
    # Build graph
    # ------------------------------------------------------------------

    if is_papers:
        extract_config = ContextExtractionConfig(
            tool_call_threshold=12,
            keep_recent_messages=6,
            messages_per_round=8,
            auto_keep_token_threshold=200,
        )
    else:
        extract_config = ContextExtractionConfig(
            tool_call_threshold=20,
            keep_recent_messages=8,
            messages_per_round=10,
            auto_keep_token_threshold=300,
        )
    extract_context_node = create_extract_context_node(
        llm=captured_llm,
        config=extract_config,
        task_context="chat",
    )

    workflow = StateGraph(ChatState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(captured_tools))
    workflow.add_node("update_state", update_state_with_tool_results)
    workflow.add_node("extract_context", extract_context_node)
    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        should_continue_from_agent,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "update_state")
    workflow.add_conditional_edges(
        "update_state",
        should_continue_from_tools,
        {"agent": "agent", "extract_context": "extract_context"},
    )
    workflow.add_edge("extract_context", "agent")

    return workflow.compile()
