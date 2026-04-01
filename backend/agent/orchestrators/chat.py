# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Chat orchestrator — thin coordinator.

The heavy lifting lives in:
- ``chat_session.py``  — SessionState / SessionManager
- ``chat_workflow.py`` — ChatState / create_chat_workflow
- ``core.prompts``     — generate_chat_prompt
"""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.llm import LLMGenerationError, create_model
from core.config import settings
from core.language_detection import detect_language_from_path
from core.prompts import DEFAULT_MAX_TOOL_CALLS
from graph.service import MemgraphIngestor
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
)
from loguru import logger

from .chat_session import SessionManager, SessionState
from .chat_workflow import create_chat_workflow
from .doc import _create_tools
from .shared import (
    extract_qualified_names,
    generate_code_block_id,
    match_node_flexible,
    process_nodes_to_explored,
    retrieve_code_cross_repo,
)

# Sentinel for global mode (no specific repo)
_GLOBAL_REPO = "__global__"


class ChatOrchestrator:
    """Universal lightweight orchestrator for quick code exploration chat.

    Not bound to a specific repository — ``repo_path`` and ``ingestor`` are
    passed per-request to ``stream_chat()``.
    """

    def __init__(self):
        try:
            config = settings.active_orchestrator_config
            self.llm = create_model(config)
            self._sessions = SessionManager()
            self._tool_cache: dict[str, tuple[list, list, list, datetime]] = {}
            self._tool_cache_ttl = 3600
            self._workflow_cache: dict[str, tuple[Any, datetime]] = {}
            logger.info(f"Chat orchestrator initialized (model: {config.model_id})")
        except Exception as e:
            raise LLMGenerationError(f"Chat orchestrator init failed: {e}") from e

    # ------------------------------------------------------------------
    # Tool / workflow caching
    # ------------------------------------------------------------------

    def _get_or_create_tools(
        self,
        repo_name: str,
        repo_path: Path,
        ingestor: MemgraphIngestor,
        page_type: str | None = None,
    ) -> tuple[list, list, list]:
        cache_key = repo_name

        if cache_key in self._tool_cache:
            update, retrieval, all_t, created = self._tool_cache[cache_key]
            if (datetime.now(UTC) - created).total_seconds() < self._tool_cache_ttl:
                return update, retrieval, all_t

        if repo_name == _GLOBAL_REPO:
            # Global mode: management tools only (no repo-specific code tools)
            from agent.tools.management_tools import create_management_tools

            mgmt_tools = create_management_tools()
            update: list = []
            retrieval: list = mgmt_tools
            all_t = mgmt_tools
        elif repo_name == "__papers__":
            # Papers mode: paper tools + code/graph tools for cross-repo exploration
            # + repo/graph management tools so users can fix auto-extraction errors
            from agent.tools.paper_tools import create_paper_tools
            from agent.tools.code_tools import (
                CodeRetriever,
                FileReader,
                create_code_explorer_tool,
                create_code_retrieval_tool,
                create_file_reader_tool,
            )
            from agent.tools.graph_query import create_all_graph_query_tools
            from agent.tools.management_tools import create_management_tools

            placeholder = "__papers__"
            paper_tools = create_paper_tools()
            graph_tools = create_all_graph_query_tools(ingestor, project_name=placeholder)
            code_explorer = create_code_explorer_tool(ingestor, project_name=placeholder)
            code_retriever = CodeRetriever(
                project_root=str(repo_path), ingestor=ingestor, project_name=placeholder
            )
            file_reader = FileReader(
                project_root=str(repo_path), ingestor=ingestor, project_name=placeholder
            )
            retrieval_tools = [
                code_explorer,
                create_code_retrieval_tool(code_retriever),
                create_file_reader_tool(file_reader),
            ]

            # Add manage_repo and manage_graph for manual repo/graph management
            mgmt_tools = create_management_tools()
            mgmt_tools = [t for t in mgmt_tools if t.name in ("manage_repo", "manage_graph")]

            update = list(graph_tools)
            retrieval = paper_tools + retrieval_tools + mgmt_tools
            all_t = update + retrieval
        else:
            update, retrieval = _create_tools(str(repo_path), ingestor, repo_name)
            # Add document editing tools for non-global repos
            from agent.tools.code_tools import (
                create_edit_doc_file_tool,
                create_read_doc_file_tool,
                create_read_doc_trace_tool,
            )

            doc_tools = [
                create_read_doc_trace_tool(),
                create_read_doc_file_tool(),
                create_edit_doc_file_tool(),
            ]
            retrieval = retrieval + doc_tools
            all_t = update + retrieval

        self._tool_cache[cache_key] = (update, retrieval, all_t, datetime.now(UTC))
        logger.info(f"Created {len(all_t)} tools for {cache_key}")
        return update, retrieval, all_t

    def _get_or_create_workflow(
        self,
        repo_name: str,
        repo_path: Path,
        ingestor: MemgraphIngestor,
    ) -> Any:
        if repo_name in self._workflow_cache:
            wf, created = self._workflow_cache[repo_name]
            if (datetime.now(UTC) - created).total_seconds() < self._tool_cache_ttl:
                return wf

        _, _, all_tools = self._get_or_create_tools(repo_name, repo_path, ingestor)
        wf = create_chat_workflow(self.llm, all_tools, repo_path, ingestor)
        self._workflow_cache[repo_name] = (wf, datetime.now(UTC))
        return wf

    # ------------------------------------------------------------------
    # Session helpers (delegate to SessionManager)
    # ------------------------------------------------------------------

    def clear_session(self, session_id: str) -> None:
        """Clear a chat session from the cache."""
        self._sessions.clear(session_id)

    def preload_session(self, session_id: str, **kwargs: Any) -> bool:
        return self._sessions.preload(session_id, **kwargs)

    @staticmethod
    def _message_signature(message: BaseMessage) -> str:
        """Build a stable signature for de-duplicating LangChain messages."""
        return json.dumps(
            message_to_dict(message),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        message: str,
        repo_name: str,
        repo_path: str,
        ingestor: MemgraphIngestor,
        max_tool_calls: int | None = None,
        session_id: str | None = None,
        context_file: str | None = None,
        page_context: dict | None = None,
        mode: str | None = None,
        skip_save_log: bool = False,
        model_override: str | None = None,
        truncate_turns: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream chat responses with real-time updates and persistent session state."""
        repo_path_obj = Path(repo_path) if isinstance(repo_path, str) else repo_path

        if max_tool_calls is None:
            max_tool_calls = DEFAULT_MAX_TOOL_CALLS

        # Per-request model override
        original_llm = None
        session_state: SessionState | None = None
        turn_messages: list[BaseMessage] = []
        turn_explored_nodes: list[dict[str, Any]] = []
        turn_tool_trace: list[dict[str, str]] = []
        turn_tool_calls = 0
        accumulated_response = ""
        persist_session_state = lambda: None
        capture_turn_messages = lambda _messages: None
        if model_override:
            try:
                from dataclasses import replace

                config = settings.active_orchestrator_config
                override_config = replace(config, model_id=model_override)
                original_llm = self.llm
                self.llm = create_model(override_config)
                if repo_name in self._workflow_cache:
                    del self._workflow_cache[repo_name]
                logger.info(f"[MODEL] Switched to {model_override}")
            except Exception as e:
                logger.warning(f"[MODEL] Override failed: {e}")

        try:
            # Session
            if not session_id:
                session_id = (
                    f"session-{int(time.time() * 1000)}-{hash(message) % 100000}"
                )

            session_state = self._sessions.get_or_create(session_id, repo_name)
            self._sessions.cleanup_stale()

            # Edit-and-regenerate: truncate session history before processing
            if truncate_turns is not None:
                session_state.truncate_to_turn(truncate_turns)
                logger.info(f"Truncated session {session_id} to {truncate_turns} turns for edit-regenerate")

            _is_papers = repo_name == "__papers__"

            # Workflow — add get_paper_doc tool when viewing a paper or a repo linked to a paper
            _needs_paper_tool = bool(
                page_context and (
                    page_context.get("source_paper_id")
                    or page_context.get("page_type") == "paper"
                )
            )
            if _needs_paper_tool:
                # Build a one-off workflow with the extra paper tool
                _, _, all_tools = self._get_or_create_tools(
                    repo_name, repo_path_obj, ingestor
                )
                # Only add get_paper_doc if not already present (e.g. __papers__ mode already has it)
                has_paper_doc = any(t.name == "get_paper_doc" for t in all_tools)
                if has_paper_doc:
                    graph = create_chat_workflow(
                        self.llm, all_tools, repo_path_obj, ingestor,
                        is_papers=_is_papers,
                    )
                else:
                    from agent.tools.paper_tools import PaperTools, GetPaperDocInput
                    from agent.tools.tool_registry import TOOL_DESCRIPTIONS
                    from langchain_core.tools import StructuredTool

                    _pt = PaperTools()
                    paper_doc_tool = StructuredTool.from_function(
                        func=_pt.get_paper_doc,
                        name="get_paper_doc",
                        description=TOOL_DESCRIPTIONS.get(
                            "get_paper_doc", "Get generated paper document"
                        ),
                        args_schema=GetPaperDocInput,
                    )
                    all_tools_with_paper = list(all_tools) + [paper_doc_tool]
                    graph = create_chat_workflow(
                        self.llm, all_tools_with_paper, repo_path_obj, ingestor,
                        is_papers=_is_papers,
                    )
            else:
                graph = self._get_or_create_workflow(
                    repo_name, repo_path_obj, ingestor
                )

            # Context injection — prefer lightweight page context, fall back to file
            if page_context:
                self._inject_page_context(
                    page_type=page_context.get("page_type"),
                    operator_name=page_context.get("operator_name"),
                    document_title=page_context.get("document_title"),
                    file_path=page_context.get("file_path"),
                    session_state=session_state,
                    source_paper_id=page_context.get("source_paper_id"),
                    source_paper_title=page_context.get("source_paper_title"),
                    selected_text=page_context.get("selected_text"),
                )
            elif context_file:
                self._inject_context_file(context_file, session_state)

            # Build initial state
            user_message = HumanMessage(content=message)
            messages = list(session_state.messages) + [user_message]

            # Paper chat: apply sliding window to limit context size
            if _is_papers and len(session_state.messages) > 0:
                messages = self._trim_messages_for_papers(
                    messages, session_state.extraction_summaries
                )
            initial_state = {
                "messages": messages,
                "explored_nodes": list(session_state.explored_nodes),
                "tool_call_count": session_state.total_tool_calls,
                "max_tool_calls": max_tool_calls,
                "turn_tool_calls": 0,
                "current_repo_name": repo_name,
                "is_global": repo_name == _GLOBAL_REPO or _is_papers,
                "is_papers": _is_papers,
                "extraction_round": session_state.extraction_round,
                "extraction_summaries": list(session_state.extraction_summaries),
                "need_extraction": False,
            }

            config = {"recursion_limit": max_tool_calls * 3}

            turn_message_signatures: set[str] = {
                self._message_signature(msg) for msg in session_state.messages
            }
            turn_explored_qns: set[str] = extract_qualified_names(
                session_state.explored_nodes
            )
            persisted_turn_tool_calls = 0
            budget_warning_emitted = False
            budget_exhausted_emitted = False

            def persist_session_state() -> None:
                self._sessions.persist(session_state)

            def capture_turn_messages(messages_to_add: list[BaseMessage]) -> None:
                nonlocal turn_messages
                new_messages: list[BaseMessage] = []
                for msg in messages_to_add:
                    if not isinstance(msg, (AIMessage, ToolMessage)):
                        continue
                    signature = self._message_signature(msg)
                    if signature in turn_message_signatures:
                        continue
                    turn_message_signatures.add(signature)
                    turn_messages.append(msg)
                    new_messages.append(msg)
                if new_messages:
                    session_state.add_messages(new_messages)
                    persist_session_state()

            def capture_turn_nodes(nodes_to_add: list[dict[str, Any]]) -> None:
                nonlocal turn_explored_nodes
                new_nodes: list[dict[str, Any]] = []
                for node in nodes_to_add:
                    qn = node.get("qualified_name")
                    if qn and qn in turn_explored_qns:
                        continue
                    if qn:
                        turn_explored_qns.add(qn)
                    turn_explored_nodes.append(node)
                    new_nodes.append(node)
                if new_nodes:
                    session_state.add_explored_nodes(new_nodes, repo_path_obj)
                    persist_session_state()

            def sync_turn_tool_calls(current_turn_tool_calls: int | None) -> None:
                nonlocal turn_tool_calls, persisted_turn_tool_calls
                if current_turn_tool_calls is None:
                    return
                turn_tool_calls = max(turn_tool_calls, current_turn_tool_calls)
                delta = current_turn_tool_calls - persisted_turn_tool_calls
                if delta > 0:
                    session_state.total_tool_calls += delta
                    persisted_turn_tool_calls = current_turn_tool_calls
                    persist_session_state()

            # Persist the user turn immediately so cancellations do not drop the prompt.
            session_state.add_messages([user_message])
            persist_session_state()

            async for event in graph.astream_events(
                initial_state, config=config, version="v2"
            ):
                event_kind = event.get("event")
                event_name = event.get("name", "")

                if event_kind == "on_chat_model_stream":
                    # Only stream tokens from the agent node, not from
                    # internal LLM calls (e.g. extract_context evaluation).
                    # LangGraph v2 puts the originating node in both tags
                    # and metadata; check all available signals.
                    event_tags = event.get("tags", [])
                    event_meta = event.get("metadata", {})
                    langgraph_node = event_meta.get("langgraph_node", "")
                    if langgraph_node and langgraph_node != "agent":
                        continue
                    if any("extract_context" in t for t in event_tags):
                        continue
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        accumulated_response += token
                        yield {
                            "type": "response",
                            "content": token,
                            "metadata": {"is_partial": True},
                        }

                elif event_kind == "on_tool_start":
                    tool_input = event.get("data", {}).get("input", {})
                    # Extract key arg for trace display
                    key_arg = ""
                    if isinstance(tool_input, dict):
                        key_arg = (
                            tool_input.get("query")
                            or tool_input.get("identifier")
                            or tool_input.get("name")
                            or tool_input.get("qualified_name")
                            or ""
                        )
                    yield {
                        "type": "tool_call",
                        "content": f"Searching: {event_name}",
                        "metadata": {"tool": event_name, "key_arg": str(key_arg)[:200]},
                    }
                    turn_tool_trace.append(
                        {"tool": event_name, "key_arg": str(key_arg)[:200]}
                    )

                elif event_kind == "on_tool_end":
                    tool_output = event.get("data", {}).get("output", "")
                    try:
                        result = (
                            json.loads(tool_output)
                            if isinstance(tool_output, str)
                            else tool_output
                        )
                        if isinstance(result, dict) and result.get("results"):
                            count = len(result["results"])
                            # Build compact preview of first few results
                            preview_items = []
                            for item in result["results"][:5]:
                                if isinstance(item, dict):
                                    name = item.get("name") or item.get("qualified_name") or ""
                                    item_type = item.get("type")
                                    if isinstance(item_type, list):
                                        item_type = item_type[0] if item_type else ""
                                    label = f"{name} ({item_type})" if item_type else name
                                    if label:
                                        preview_items.append(label)
                            yield {
                                "type": "tool_result",
                                "content": f"Found {count} code elements",
                                "metadata": {
                                    "count": count,
                                    "preview": preview_items,
                                },
                            }
                            if turn_tool_trace:
                                turn_tool_trace[-1]["result"] = f"{count} results"
                                if preview_items:
                                    turn_tool_trace[-1]["preview"] = preview_items
                        elif isinstance(result, dict) and result.get("hierarchy_tree"):
                            # Structural queries return hierarchy_tree instead of results
                            tree = result["hierarchy_tree"]
                            # Show first few lines of the tree
                            tree_lines = [l for l in tree.split("\n") if l.strip()][:6]
                            yield {
                                "type": "tool_result",
                                "content": f"Found hierarchy tree",
                                "metadata": {
                                    "preview": tree_lines,
                                },
                            }
                            if turn_tool_trace:
                                turn_tool_trace[-1]["result"] = f"tree"
                                turn_tool_trace[-1]["preview"] = tree_lines
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass

                elif event_kind == "on_chain_end":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        current_turn_tool_calls = output.get("turn_tool_calls")
                        if isinstance(current_turn_tool_calls, int):
                            if (
                                not budget_warning_emitted
                                and current_turn_tool_calls >= max(1, int(max_tool_calls * 0.8))
                                and current_turn_tool_calls < max_tool_calls
                            ):
                                budget_warning_emitted = True
                                warning_text = (
                                    f"Tool budget getting low ({current_turn_tool_calls}/{max_tool_calls}); "
                                    "wrapping up exploration soon."
                                )
                                yield {
                                    "type": "tool_call",
                                    "content": warning_text,
                                    "metadata": {
                                        "tool": "budget",
                                        "key_arg": f"{current_turn_tool_calls}/{max_tool_calls}",
                                    },
                                }
                                turn_tool_trace.append(
                                    {
                                        "tool": "budget",
                                        "key_arg": f"{current_turn_tool_calls}/{max_tool_calls}",
                                        "result": "near limit",
                                    }
                                )

                            if (
                                not budget_exhausted_emitted
                                and current_turn_tool_calls >= max_tool_calls
                            ):
                                budget_exhausted_emitted = True
                                exhausted_text = (
                                    f"Tool budget reached ({current_turn_tool_calls}/{max_tool_calls}); "
                                    "generating the final answer from gathered context."
                                )
                                yield {
                                    "type": "tool_call",
                                    "content": exhausted_text,
                                    "metadata": {
                                        "tool": "budget",
                                        "key_arg": f"{current_turn_tool_calls}/{max_tool_calls}",
                                    },
                                }
                                turn_tool_trace.append(
                                    {
                                        "tool": "budget",
                                        "key_arg": f"{current_turn_tool_calls}/{max_tool_calls}",
                                        "result": "final answer mode",
                                    }
                                )

                        output_messages = output.get("messages", [])
                        if isinstance(output_messages, list):
                            capture_turn_messages(output_messages)

                        output_nodes = output.get("explored_nodes", [])
                        if isinstance(output_nodes, list):
                            capture_turn_nodes(output_nodes)

                        sync_turn_tool_calls(current_turn_tool_calls)

                        final_round = output.get(
                            "extraction_round", session_state.extraction_round
                        )
                        if final_round != session_state.extraction_round:
                            session_state.extraction_round = final_round
                            persist_session_state()
                        for s in output.get("extraction_summaries", []):
                            if s not in session_state.extraction_summaries:
                                session_state.extraction_summaries.append(s)
                                persist_session_state()

                        if event_name == "LangGraph" and accumulated_response and not any(
                            isinstance(msg, AIMessage) and msg.content == accumulated_response
                            for msg in turn_messages
                        ):
                            capture_turn_messages([AIMessage(content=accumulated_response)])

            # Parse references & build code blocks
            final_response = accumulated_response or ""
            if not final_response:
                for msg in reversed(turn_messages):
                    if isinstance(msg, AIMessage) and msg.content:
                        final_response = msg.content
                        break

            turn_id = str(int(time.time() * 1000))
            parsed_data = await asyncio.to_thread(
                self._parse_code_references,
                final_response,
                session_state.explored_nodes,
                turn_id,
                repo_path_obj,
                ingestor,
            )

            newly_discovered = parsed_data.get("newly_discovered_nodes", [])
            if newly_discovered:
                session_state.add_explored_nodes(newly_discovered, repo_path_obj)
                persist_session_state()

            all_accumulated_blocks = session_state.get_accumulated_code_blocks(
                repo_path_obj
            )
            referenced_block_ids = await asyncio.to_thread(
                self._collect_referenced_block_ids,
                session_state,
                all_accumulated_blocks,
                parsed_data,
                repo_name,
                repo_path_obj,
                ingestor,
            )

            filtered_blocks = [
                b for b in all_accumulated_blocks if b["id"] in referenced_block_ids
            ]

            chat_log_path = None
            if not skip_save_log:
                full_messages = list(session_state.messages)
                chat_log_path = await asyncio.to_thread(
                    self._save_chat_log,
                    message,
                    turn_messages,
                    final_response,
                    repo_name,
                    parsed_data,
                    session_id,
                    turn_tool_calls,
                    full_messages,
                    truncate_turns,
                    False,
                    turn_tool_trace,
                )

            yield {
                "type": "complete",
                "content": "",
                "metadata": {
                    "explored_nodes": turn_explored_nodes,
                    "tool_calls": turn_tool_calls,
                    "total_tool_calls": session_state.total_tool_calls,
                    "session_total_nodes": len(session_state.explored_nodes),
                    "chat_log_path": chat_log_path,
                    "references": parsed_data.get("references", []),
                    "code_blocks": parsed_data.get("code_blocks", []),
                    "accumulated_code_blocks": filtered_blocks,
                    "tool_trace": turn_tool_trace,
                },
            }

        except asyncio.CancelledError:
            logger.info(f"Chat stream interrupted for session {session_id}")
            if accumulated_response and not any(
                isinstance(msg, AIMessage) and msg.content == accumulated_response
                for msg in turn_messages
            ):
                capture_turn_messages([AIMessage(content=accumulated_response)])
            if session_state is not None:
                persist_session_state()

            if session_state is not None and not skip_save_log and (
                accumulated_response.strip() or turn_messages or turn_explored_nodes
            ):
                partial_response = accumulated_response or ""
                await asyncio.to_thread(
                    self._save_chat_log,
                    message,
                    turn_messages,
                    partial_response,
                    repo_name,
                    {"references": []},
                    session_id,
                    turn_tool_calls,
                    list(session_state.messages),
                    truncate_turns,
                    True,
                    turn_tool_trace,
                )
            raise
        except Exception as e:
            logger.error(f"Chat streaming error: {e}")
            yield {"type": "error", "content": f"Error: {str(e)}", "metadata": {}}
        finally:
            if original_llm is not None:
                self.llm = original_llm
                if repo_name in self._workflow_cache:
                    del self._workflow_cache[repo_name]

    # ------------------------------------------------------------------
    # Paper chat sliding window
    # ------------------------------------------------------------------

    @staticmethod
    def _trim_messages_for_papers(
        messages: list[BaseMessage],
        extraction_summaries: list[str],
        max_turns: int = 4,
        max_tool_output_chars: int = 4000,
    ) -> list[BaseMessage]:
        """Trim old turns for paper chat, keeping last N complete turns + summary prefix.

        A "turn" starts at each ``HumanMessage``.  We scan backwards to find the
        *max_turns*-th ``HumanMessage`` and discard everything before it.  If there
        are ``extraction_summaries`` from earlier rounds, they are prepended as a
        single ``SystemMessage`` so the model retains high-level context.

        Additionally, truncates oversized tool outputs in **older** turns (all but
        the most recent turn) to prevent massive payloads from hitting the API.
        """
        # Find positions of all HumanMessages
        human_indices = [
            i for i, m in enumerate(messages) if isinstance(m, HumanMessage)
        ]

        trimmed = messages
        if len(human_indices) > max_turns:
            # Cut point: keep from the (max_turns)-th-from-last HumanMessage onward
            cut_index = human_indices[-max_turns]
            trimmed = messages[cut_index:]

        # Truncate oversized tool outputs in older turns (not the current turn).
        # The current turn starts at the last HumanMessage.
        trimmed_human_indices = [
            i for i, m in enumerate(trimmed) if isinstance(m, HumanMessage)
        ]
        last_human_idx = trimmed_human_indices[-1] if trimmed_human_indices else len(trimmed)
        truncated_count = 0
        result = []
        for i, msg in enumerate(trimmed):
            if (
                i < last_human_idx
                and isinstance(msg, ToolMessage)
                and len(msg.content) > max_tool_output_chars
            ):
                result.append(
                    ToolMessage(
                        content=msg.content[:max_tool_output_chars] + "\n\n[... truncated for context window ...]",
                        tool_call_id=msg.tool_call_id,
                        id=msg.id,
                    )
                )
                truncated_count += 1
            else:
                result.append(msg)
        trimmed = result

        # Prepend extraction summaries so the model has prior context
        if extraction_summaries:
            summary_text = (
                "Summary of earlier conversation turns:\n"
                + "\n---\n".join(extraction_summaries)
            )
            trimmed = [SystemMessage(content=summary_text)] + trimmed

        if len(trimmed) != len(messages) or truncated_count > 0:
            logger.info(
                f"Paper chat sliding window: {len(messages)} msgs → {len(trimmed)} "
                f"(kept last {max_turns} turns, {len(extraction_summaries)} summaries, "
                f"{truncated_count} tool outputs truncated)"
            )
        return trimmed

    # ------------------------------------------------------------------
    # Lightweight page context injection
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_page_context(
        page_type: str | None,
        operator_name: str | None,
        document_title: str | None,
        file_path: str | None,
        session_state: SessionState,
        source_paper_id: str | None = None,
        source_paper_title: str | None = None,
        selected_text: str | None = None,
    ) -> None:
        """Inject lightweight page context metadata into the session.

        Instead of injecting full document content, this provides the agent
        with awareness of what page the user is viewing. The agent can use
        its tools to fetch content if needed.
        """
        if not page_type:
            return

        # For papers page, always replace context with latest filtered list
        if page_type == "papers":
            # Remove any previous papers context message
            PAPERS_MARKER = "PAGE_CONTEXT: papers"
            session_state.messages = [
                msg for msg in session_state.messages
                if not (isinstance(msg, AIMessage) and PAPERS_MARKER in msg.content)
            ]
            parts = [PAPERS_MARKER]
            parts.append(
                f"The user is browsing the Daily Papers page. **{document_title or 'Daily Papers'}**."
            )
            if selected_text:
                parts.append(
                    "\nPapers currently visible on the user's screen:\n"
                    f"{selected_text}"
                )
            parts.append(
                "\nYou can use `browse_papers()` or `browse_papers(mode=\"range\", start_date=..., end_date=...)` to fetch more papers. "
                "Use `read_paper(arxiv_id=...)` to process a specific paper for detailed reading."
            )
            context_msg = AIMessage(content="\n".join(parts))
            session_state.add_messages([context_msg])
            logger.info(f"Injected papers page context: {document_title}")
            return

        # Avoid duplicate injection for other page types
        context_marker = f"PAGE_CONTEXT: {page_type}:{operator_name or 'none'}"
        for msg in session_state.messages:
            if isinstance(msg, AIMessage) and context_marker in msg.content:
                return

        parts = [context_marker]
        if page_type == "operator" and operator_name:
            parts.append(
                f"The user is currently viewing the documentation for **{operator_name}**."
            )
            if file_path:
                parts.append(f"Document path: `{file_path}`")
            parts.append(
                "You have document editing tools available:\n"
                "- `read_doc_trace`: Read the generation trace to understand context\n"
                "- `read_doc_file`: Read the .md file with line numbers\n"
                "- `edit_doc_file`: Edit specific lines or rewrite the entire file"
            )
        elif page_type == "repo" and document_title:
            parts.append(
                f"The user is on the repository overview page for **{document_title}**."
            )
            if source_paper_id:
                paper_label = source_paper_title or source_paper_id
                parts.append(
                    f"\nThis repository was built from the paper: **{paper_label}** (ID: `{source_paper_id}`).\n"
                    "If the user asks about ablation experiments, methodology details, results, "
                    "or other paper-specific content, use `get_paper_doc` with the paper ID above "
                    "to retrieve the parsed paper content."
                )
        elif page_type == "paper" and document_title:
            parts.append(
                f"The user is currently viewing the paper: **{document_title}**."
            )
            if file_path:
                parts.append(f"arXiv ID / paper_id: `{file_path}`")
            parts.append(
                "This paper has already been processed. "
                f"Call `get_paper_doc(paper_id=\"{file_path}\")` directly to retrieve its parsed content. "
                "Do NOT call list_papers() or read_paper() — the paper is already available. "
                "For code analysis, use `list_repos()` to find the associated repo."
            )
        elif page_type == "home":
            parts.append("The user is on the AtCode home page.")

        if parts:
            context_msg = AIMessage(content="\n".join(parts))
            session_state.add_messages([context_msg])
            logger.info(f"Injected page context: {page_type}/{operator_name}")

    # ------------------------------------------------------------------
    # Context file injection
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_context_file(context_file: str, session_state: SessionState) -> None:
        """Inject a context file (documentation JSON) into the session."""
        import urllib.parse

        context_file = urllib.parse.unquote(context_file)
        if not Path(context_file).exists():
            logger.warning(f"Context file not found: {context_file}")
            return

        context_marker = f"CONTEXT_SOURCE: {context_file}"
        for msg in session_state.messages:
            if isinstance(msg, AIMessage) and context_marker in msg.content:
                return  # already loaded

        try:
            with open(context_file, encoding="utf-8") as f:
                doc_data = json.load(f)
            markdown_content = doc_data.get("markdown", "")
            operator_name = doc_data.get("operator_name", Path(context_file).stem)
            if markdown_content:
                context_msg = AIMessage(
                    content=f"{context_marker}\n\n"
                    f"**I have retrieved the documentation for {operator_name}:**\n\n"
                    f"{markdown_content}\n\n---\n"
                    f"I've loaded the documentation above. What questions do you have about {operator_name}?"
                )
                session_state.add_messages([context_msg])
                logger.info(f"Injected context from {context_file}")
        except Exception as e:
            logger.error(f"Failed to load context file {context_file}: {e}")

    # ------------------------------------------------------------------
    # Reference parsing (unchanged logic from original)
    # ------------------------------------------------------------------

    def _parse_code_references(
        self,
        content: str,
        explored_nodes: list[dict],
        turn_id: str,
        repo_path: Path,
        ingestor: MemgraphIngestor,
    ) -> dict[str, Any]:
        """Parse [[...]] references from content for dual-column structure."""
        node_id_pattern = r"\[\[([^\]]+)\]\]"
        seen_ids: set[str] = set()
        node_identifiers = []
        for match in re.finditer(node_id_pattern, content):
            nid = match.group(1)
            if nid not in seen_ids:
                node_identifiers.append(nid)
                seen_ids.add(nid)

        if not node_identifiers:
            return {"references": [], "code_blocks": [], "newly_discovered_nodes": []}

        logger.info(f"Parsing {len(node_identifiers)} code references")

        references: list[dict] = []
        code_blocks: list[dict] = []
        newly_discovered: list[dict] = []
        existing_qns = {
            n.get("qualified_name") for n in explored_nodes if n.get("qualified_name")
        }

        for node_id in node_identifiers:
            matched = match_node_flexible(node_id, explored_nodes, ingestor, repo_path)
            if matched:
                qn = matched.get("qualified_name", node_id)
                if qn and qn not in existing_qns:
                    newly_discovered.append(matched)
                    existing_qns.add(qn)

                file_path = matched.get("path", "")
                start_line = matched.get("start_line")
                end_line = matched.get("end_line")
                node_type = matched.get("type", "Unknown")
                if isinstance(node_type, list):
                    node_type = node_type[0] if node_type else "Unknown"

                code = matched.get("code")
                if not code and file_path and start_line and end_line:
                    code = retrieve_code_cross_repo(
                        repo_path, qn, file_path, start_line, end_line
                    )

                ref_data: dict[str, Any] = {
                    "identifier": qn,
                    "qualified_name": qn,
                    "name": matched.get("name", node_id.split(".")[-1]),
                    "type": node_type,
                }
                if file_path:
                    ref_data["path"] = file_path
                if start_line is not None:
                    ref_data["start_line"] = start_line
                if end_line is not None:
                    ref_data["end_line"] = end_line
                if code:
                    ref_data["code"] = code
                    ref_data["language"] = (
                        detect_language_from_path(file_path) if file_path else "text"
                    )
                references.append(ref_data)

                if code and file_path and start_line and end_line:
                    language = detect_language_from_path(file_path)
                    block_id = generate_code_block_id(file_path, start_line, end_line)
                    if not any(b["id"] == block_id for b in code_blocks):
                        code_blocks.append(
                            {
                                "id": block_id,
                                "file": file_path,
                                "startLine": start_line,
                                "endLine": end_line,
                                "code": code,
                                "language": language,
                                "qualified_name": qn,
                            }
                        )
                elif node_type == "Folder" and file_path:
                    self._handle_folder_reference(
                        repo_path, file_path, qn, code_blocks, ref_data
                    )
                elif node_type in ("File", "Folder"):
                    ref_data["node_only"] = True
            else:
                # Fallback DB search
                fallback = self._fallback_db_search(node_id, ingestor, repo_path)
                if fallback:
                    references.append(fallback)
                else:
                    references.append(
                        {
                            "identifier": node_id,
                            "qualified_name": node_id,
                            "name": node_id.split(".")[-1],
                            "type": "Unresolved",
                        }
                    )

        logger.info(
            f"Parsed {len(references)} refs, {len(code_blocks)} blocks, {len(newly_discovered)} new nodes"
        )
        return {
            "references": references,
            "code_blocks": code_blocks,
            "newly_discovered_nodes": newly_discovered,
        }

    @staticmethod
    def _handle_folder_reference(
        repo_path: Path,
        file_path: str,
        qn: str,
        code_blocks: list[dict[str, Any]],
        ref_data: dict[str, Any],
    ) -> None:
        """Handle [[...]] references that point to folder nodes."""
        init_file = repo_path / file_path / "__init__.py"
        if init_file.exists():
            try:
                init_code = init_file.read_text(encoding="utf-8")
                line_count = len(init_code.splitlines())
                init_path = f"{file_path}/__init__.py"
                block_id = generate_code_block_id(init_path, 1, line_count)
                if not any(b["id"] == block_id for b in code_blocks):
                    code_blocks.append(
                        {
                            "id": block_id,
                            "file": init_path,
                            "startLine": 1,
                            "endLine": line_count,
                            "code": init_code,
                            "language": "python",
                            "qualified_name": qn,
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to read __init__.py for {qn}: {e}")
        else:
            ref_data["node_only"] = True

    @staticmethod
    def _fallback_db_search(
        node_id: str, ingestor: MemgraphIngestor, repo_path: Path
    ) -> dict[str, Any] | None:
        """Search the graph database as a fallback when node matching fails."""
        if "." not in node_id:
            return None
        parts = node_id.split(".")
        suffix = f".{parts[-1]}"
        try:
            results = ingestor.fetch_all(
                "MATCH (n) WHERE n.qualified_name ENDS WITH $suffix AND n.name = $name "
                "OPTIONAL MATCH (f:File)-[:DEFINES]->(n) "
                "RETURN n.qualified_name AS qualified_name, n.name AS name, labels(n) AS type, "
                "COALESCE(n.path, f.path) AS path, n.start_line AS start_line, n.end_line AS end_line, "
                "n.docstring AS docstring LIMIT 5",
                {"suffix": suffix, "name": parts[-1]},
            )
            if not results:
                return None
            best = next(
                (r for r in results if parts[0] in r.get("qualified_name", "")),
                results[0],
            )
            qn = best.get("qualified_name", node_id)
            file_path = best.get("path")
            start_line = best.get("start_line")
            end_line = best.get("end_line")
            code = None
            if file_path and start_line and end_line:
                code = retrieve_code_cross_repo(
                    repo_path, qn, file_path, start_line, end_line
                )
            node_type = best.get("type", ["Unknown"])
            if isinstance(node_type, list):
                node_type = node_type[0] if node_type else "Unknown"
            ref: dict[str, Any] = {
                "identifier": qn,
                "qualified_name": qn,
                "name": best.get("name", node_id.split(".")[-1]),
                "type": node_type,
            }
            if file_path:
                ref["path"] = file_path
            if start_line is not None:
                ref["start_line"] = start_line
            if end_line is not None:
                ref["end_line"] = end_line
            rn = qn.split(".")[0] if "." in qn else None
            if rn:
                ref["repo_name"] = rn
            if code:
                ref["code"] = code
                ref["language"] = (
                    detect_language_from_path(file_path) if file_path else "text"
                )
            return ref
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Referenced block collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_referenced_block_ids(
        session_state: SessionState,
        all_blocks: list[dict],
        parsed_data: dict,
        repo_name: str,
        repo_path_obj: Path,
        ingestor: MemgraphIngestor,
    ) -> set[str]:
        """Build set of block IDs that are explicitly referenced in the conversation."""
        referenced: set[str] = set()

        # Index
        accumulated_index: dict[tuple, dict] = {}
        for block in all_blocks:
            key = (block.get("file"), block.get("startLine"), block.get("endLine"))
            if key[0] and key[1] and key[2]:
                accumulated_index[key] = block

        # Scan all AI messages for [[...]] refs
        for msg in session_state.messages:
            if isinstance(msg, AIMessage) and msg.content:
                refs = re.findall(r"\[\[([^\]]+)\]\]", msg.content)
                for ref in refs:
                    for block in all_blocks:
                        qn = block.get("qualified_name", "")
                        if (
                            qn == ref
                            or qn.endswith("." + ref)
                            or (
                                len(qn.split(".")) >= 2
                                and ".".join(qn.split(".")[-2:]) == ref
                            )
                        ):
                            referenced.add(block["id"])

        # Fallback completion from parsed references
        for ref_data in parsed_data.get("references", []):
            fp = ref_data.get("path")
            sl = ref_data.get("start_line")
            el = ref_data.get("end_line")
            qn = ref_data.get("qualified_name", "") or ref_data.get("identifier", "")
            if not (fp and isinstance(sl, int) and isinstance(el, int)):
                continue
            key = (fp, sl, el)
            if key in accumulated_index:
                referenced.add(accumulated_index[key]["id"])
                continue

            # Read code from disk
            try:
                actual_repo = repo_name
                if qn:
                    parts = qn.split(".")
                    if parts:
                        actual_repo = parts[0]
                project_path = ingestor.get_project_path(actual_repo)
                if project_path:
                    repo_root = Path(project_path)
                    if not repo_root.exists():
                        from core.config import get_wiki_repos_dir

                        new_root = get_wiki_repos_dir() / actual_repo
                        if new_root.exists():
                            repo_root = new_root
                else:
                    from core.config import get_wiki_repos_dir

                    repo_root = get_wiki_repos_dir() / actual_repo
                file_content = (repo_root / fp).read_text(encoding="utf-8")
                lines = file_content.split("\n")
                code = "\n".join(lines[sl - 1 : el])
            except Exception:
                continue

            if not code:
                continue
            language = detect_language_from_path(fp)
            block_id = generate_code_block_id(fp, sl, el)
            new_block = {
                "id": block_id,
                "file": fp,
                "startLine": sl,
                "endLine": el,
                "code": code,
                "language": language,
                "qualified_name": qn,
            }
            all_blocks.append(new_block)
            accumulated_index[key] = new_block
            referenced.add(block_id)

        return referenced

    # ------------------------------------------------------------------
    # Chat log persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _save_chat_log(
        query: str,
        turn_messages: list[BaseMessage],
        response: str,
        repo_name: str,
        parsed_data: dict[str, Any],
        session_id: str | None = None,
        turn_tool_calls: int = 0,
        full_messages: list[BaseMessage] | None = None,
        truncate_turns: int | None = None,
        interrupted: bool = False,
        tool_trace: list[dict[str, str]] | None = None,
    ) -> str:
        from core.config import get_wiki_chat_dir
        from core.file_lock import FileLock
        from langchain_core.messages import message_to_dict

        chat_dir = get_wiki_chat_dir() / repo_name
        chat_dir.mkdir(parents=True, exist_ok=True)

        chat_id = (
            session_id
            or hashlib.md5((query + str(datetime.now())).encode()).hexdigest()[:12]
        )
        turn = {
            "query": query,
            "response": response,
            "references": parsed_data.get("references", []),
            "tool_calls": turn_tool_calls,
            "tool_trace": tool_trace or [],
            "timestamp": datetime.now().isoformat(),
            "interrupted": interrupted,
        }
        file_path = chat_dir / f"{chat_id}.json"

        serialized_messages = None
        if full_messages:
            try:
                serialized_messages = [message_to_dict(msg) for msg in full_messages]
            except Exception as e:
                logger.warning(f"Failed to serialize messages: {e}")

        with FileLock(file_path, timeout=10.0):
            if file_path.exists():
                try:
                    with open(file_path, encoding="utf-8") as f:
                        chat_log = json.load(f)
                    if "turns" not in chat_log:
                        old_turn = {
                            "query": chat_log.get("query", ""),
                            "response": chat_log.get("response", ""),
                            "references": chat_log.get("references", []),
                            "tool_calls": chat_log.get("tool_calls", 0),
                            "timestamp": chat_log.get(
                                "timestamp", datetime.now().isoformat()
                            ),
                        }
                        chat_log["turns"] = [old_turn]
                    # Edit-and-regenerate: truncate turns before appending
                    if truncate_turns is not None:
                        chat_log["turns"] = chat_log["turns"][:truncate_turns]
                    chat_log["turns"].append(turn)
                    chat_log["updated_at"] = datetime.now().isoformat()
                    if serialized_messages is not None:
                        chat_log["messages"] = serialized_messages
                except Exception:
                    chat_log = {
                        "id": chat_id,
                        "repo_name": repo_name,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                        "turns": [turn],
                    }
                    if serialized_messages is not None:
                        chat_log["messages"] = serialized_messages
            else:
                chat_log = {
                    "id": chat_id,
                    "repo_name": repo_name,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "turns": [turn],
                }
                if serialized_messages is not None:
                    chat_log["messages"] = serialized_messages

            temp_path = file_path.with_suffix(".tmp")
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(chat_log, f, ensure_ascii=False, indent=2)
                temp_path.replace(file_path)
            except Exception as e:
                logger.error(f"Failed to write chat log: {e}")
                if temp_path.exists():
                    temp_path.unlink()
                raise

        logger.info(f"Chat log saved: {file_path} ({len(chat_log['turns'])} turns)")
        return str(file_path)


def create_chat_orchestrator(mode: str = "default") -> ChatOrchestrator:
    """Factory function to create a universal chat orchestrator.

    Args:
        mode: Ignored (kept for backward compatibility). Modes were removed.
    """
    logger.info("Creating Chat Orchestrator...")
    orchestrator = ChatOrchestrator()
    logger.info("Chat Orchestrator created successfully")
    return orchestrator
