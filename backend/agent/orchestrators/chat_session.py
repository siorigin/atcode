# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Session state management for the chat orchestrator.

Extracted from chat.py for readability and testability.
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.language_detection import detect_language_from_path
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    messages_from_dict,
    messages_to_dict,
)
from loguru import logger

from .shared import (
    extract_qualified_names,
    generate_code_block_id,
    retrieve_code_cross_repo,
)


class SessionState:
    """Persistent state for a chat session across multiple turns."""

    def __init__(self, session_id: str, repo_name: str | None = None) -> None:
        self.session_id = session_id
        self.repo_name = repo_name
        self.messages: list[BaseMessage] = []
        self.explored_nodes: list[dict[str, Any]] = []
        self.total_tool_calls: int = 0
        self.last_active = time.time()
        self.created_at = time.time()
        self._association_hash = self._compute_association_hash()
        # Context extraction state
        self.extraction_round: int = 0
        self.extraction_summaries: list[str] = []

    def _compute_association_hash(self) -> str:
        content = f"{self.session_id}:{self.repo_name or 'unknown'}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def validate_association(self, repo_name: str) -> bool:
        """Check if the given repo_name matches this session's association."""
        if not self.repo_name:
            return True
        return self.repo_name == repo_name

    def add_messages(self, messages: list[BaseMessage]) -> None:
        """Append messages and update last active timestamp."""
        self.messages.extend(messages)
        self.last_active = time.time()

    MAX_EXPLORED_NODES = 200

    def add_explored_nodes(
        self, nodes: list[dict[str, Any]], repo_path: Path | None = None
    ) -> None:
        """Add new explored nodes, deduplicating by qualified_name.

        When the total exceeds MAX_EXPLORED_NODES, apply a sliding window:
        - Always keep "entry" nodes (discovered via find_nodes)
        - Fill remaining quota with the most recent nodes
        """
        existing_qns = extract_qualified_names(self.explored_nodes)
        for node in nodes:
            qn = node.get("qualified_name")
            if qn and qn not in existing_qns:
                if repo_path and "_repo_path" not in node:
                    node["_repo_path"] = str(repo_path)
                self.explored_nodes.append(node)
                existing_qns.add(qn)

        # Sliding window eviction
        if len(self.explored_nodes) > self.MAX_EXPLORED_NODES:
            entry_nodes = [
                n for n in self.explored_nodes
                if n.get("tool_used") == "find_nodes"
            ]
            non_entry = [
                n for n in self.explored_nodes
                if n.get("tool_used") != "find_nodes"
            ]
            remaining_quota = max(0, self.MAX_EXPLORED_NODES - len(entry_nodes))
            kept_recent = non_entry[-remaining_quota:] if remaining_quota > 0 else []
            self.explored_nodes = entry_nodes + kept_recent
            logger.debug(
                f"Sliding window: kept {len(entry_nodes)} entry + {len(kept_recent)} recent "
                f"= {len(self.explored_nodes)} nodes"
            )

        self.last_active = time.time()

    def truncate_to_turn(self, keep_turns: int) -> None:
        """Truncate message history to keep only the first `keep_turns` user-assistant exchanges."""
        human_count = 0
        cut_index = len(self.messages)
        for i, msg in enumerate(self.messages):
            if isinstance(msg, HumanMessage):
                if human_count == keep_turns:
                    cut_index = i
                    break
                human_count += 1
        self.messages = self.messages[:cut_index]
        # Reset counters for the new branch
        self.total_tool_calls = 0
        self.extraction_round = 0
        self.extraction_summaries = []
        # Keep explored_nodes — they're still valid knowledge
        self.last_active = time.time()

    def get_accumulated_code_blocks(
        self, repo_path: Path = None
    ) -> list[dict[str, Any]]:
        """Get all unique code blocks from explored nodes."""
        code_blocks = []
        seen_blocks: set[tuple] = set()

        for node in self.explored_nodes:
            file_path = node.get("path")
            start_line = node.get("start_line")
            end_line = node.get("end_line")

            if not (file_path and start_line and end_line):
                continue

            block_key = (file_path, start_line, end_line)
            if block_key in seen_blocks:
                continue
            seen_blocks.add(block_key)

            code = node.get("code")
            if not code:
                qualified_name = node.get("qualified_name")
                node_repo_path = node.get("_repo_path")
                if node_repo_path:
                    code = retrieve_code_cross_repo(
                        Path(node_repo_path),
                        qualified_name,
                        file_path,
                        start_line,
                        end_line,
                    )
                elif repo_path:
                    code = retrieve_code_cross_repo(
                        repo_path, qualified_name, file_path, start_line, end_line
                    )

            if code:
                language = detect_language_from_path(file_path)
                block_id = generate_code_block_id(file_path, start_line, end_line)
                code_blocks.append(
                    {
                        "id": block_id,
                        "file": file_path,
                        "startLine": start_line,
                        "endLine": end_line,
                        "code": code,
                        "language": language,
                        "qualified_name": node.get("qualified_name", ""),
                    }
                )

        return code_blocks

    def to_dict(self, include_payload: bool = False) -> dict[str, Any]:
        """Serialize session state to a dictionary."""
        data = {
            "session_id": self.session_id,
            "repo_name": self.repo_name,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "last_active": datetime.fromtimestamp(self.last_active).isoformat(),
            "total_tool_calls": self.total_tool_calls,
            "association_hash": self._association_hash,
            "explored_nodes_count": len(self.explored_nodes),
            "messages_count": len(self.messages),
            "extraction_round": self.extraction_round,
            "extraction_summaries_count": len(self.extraction_summaries),
        }
        if include_payload:
            data["messages"] = messages_to_dict(self.messages)
            data["explored_nodes"] = self.explored_nodes
            data["extraction_summaries"] = self.extraction_summaries
        return data

    def get_context_summary(self) -> str:
        """Build a markdown summary of accumulated context for the LLM."""
        summary = ""
        if self.explored_nodes:
            summary += f"\n\n**ACCUMULATED CONTEXT ({len(self.explored_nodes)} explored elements):**\n"
            for node in self.explored_nodes[-15:]:
                qn = node.get("qualified_name", "Unknown")
                node_type = node.get("type", "Unknown")
                summary += f"- {qn} ({node_type})\n"
            if len(self.explored_nodes) > 15:
                summary += f"... and {len(self.explored_nodes) - 15} more elements\n"

        human_messages = [m for m in self.messages if isinstance(m, HumanMessage)]
        if human_messages:
            summary += f"\n**CONVERSATION HISTORY:** {len(human_messages)} previous question(s)\n"
            summary += "Use the accumulated context from previous turns to answer follow-up questions.\n"
            summary += "If the question can be answered with existing context, respond directly WITHOUT calling tools.\n"
            summary += "Only call tools if you need NEW information not in the conversation history.\n"

        return summary


class SessionManager:
    """Manages session cache, persistence, and lifecycle."""

    def __init__(self, cache_ttl: int = 3600) -> None:
        self._cache: dict[str, SessionState] = {}
        self._cache_ttl = cache_ttl

    def get_or_create(
        self, session_id: str, repo_name: str | None = None
    ) -> SessionState:
        """Get an existing session or create a new one."""
        if session_id not in self._cache:
            session = SessionState(session_id, repo_name)
            self._cache[session_id] = session
            logger.info(
                f"Created new session state: {session_id} for repo: {repo_name}"
            )
        else:
            session = self._cache[session_id]
            if repo_name and not session.validate_association(repo_name):
                raise ValueError(
                    f"Session {session_id} is associated with a different repository"
                )
            if not session.repo_name and repo_name:
                session.repo_name = repo_name
        return session

    def clear(self, session_id: str) -> None:
        """Remove a session from the cache."""
        if session_id in self._cache:
            del self._cache[session_id]
            logger.info(f"Cleared session from cache: {session_id}")

    def cleanup_stale(self, max_age_seconds: int = 3600) -> None:
        """Remove sessions that have been inactive longer than max_age_seconds."""
        current_time = time.time()
        stale = [
            sid
            for sid, s in self._cache.items()
            if current_time - s.last_active > max_age_seconds
        ]
        for sid in stale:
            del self._cache[sid]
            logger.info(f"Cleaned up stale session: {sid}")

    def persist(self, session_state: SessionState) -> None:
        """Persist full session state to disk as JSON."""
        if not session_state.repo_name:
            logger.warning(
                f"Cannot persist session {session_state.session_id}: no repo_name"
            )
            return
        try:
            from core.config import get_wiki_chat_dir

            session_dir = get_wiki_chat_dir() / session_state.repo_name / ".sessions"
            session_dir.mkdir(parents=True, exist_ok=True)
            session_file = session_dir / f"{session_state.session_id}.state.json"
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(
                    session_state.to_dict(include_payload=True),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.debug(f"Persisted session state: {session_file}")
        except Exception as e:
            logger.error(f"Failed to persist session state: {e}")

    def preload(
        self,
        session_id: str,
        chat_log_path: str | None = None,
        repo_name: str | None = None,
        repo_path: Path | str | None = None,
    ) -> bool:
        """Preload a session from a saved chat log."""
        try:
            repo_path_obj = Path(repo_path) if repo_path else None
            session_state = self.get_or_create(session_id, repo_name)

            if len(session_state.messages) > 0:
                logger.info(
                    f"Session {session_id} already cached, skipping disk preload"
                )
                return True

            if repo_name:
                state_file = self._session_file_path(repo_name, session_id)
                if state_file.exists() and self._restore_from_state_file(
                    state_file, session_state
                ):
                    logger.info(
                        f"Preloaded session {session_id} from state file: "
                        f"{len(session_state.messages)} msgs, "
                        f"{len(session_state.explored_nodes)} nodes, "
                        f"{session_state.total_tool_calls} tool calls"
                    )
                    return True

            if not chat_log_path and repo_name:
                from core.config import get_wiki_chat_dir

                chat_dir = get_wiki_chat_dir() / repo_name
                chat_log_path = str(chat_dir / f"{session_id}.json")

            if not chat_log_path or not Path(chat_log_path).exists():
                logger.info(
                    f"No existing chat log for session {session_id}, starting fresh"
                )
                return False

            with open(chat_log_path, encoding="utf-8") as f:
                chat_log = json.load(f)

            if "messages" in chat_log:
                try:
                    restored_messages = messages_from_dict(chat_log["messages"])
                    session_state.messages = restored_messages
                    logger.info(
                        f"Restored {len(restored_messages)} messages from chat log"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to restore messages: {e}, falling back to turns"
                    )
                    self._reconstruct_from_turns(chat_log, session_state, repo_path_obj)
            else:
                self._reconstruct_from_turns(chat_log, session_state, repo_path_obj)

            logger.info(
                f"Preloaded session {session_id}: "
                f"{len(session_state.messages)} msgs, "
                f"{len(session_state.explored_nodes)} nodes, "
                f"{session_state.total_tool_calls} tool calls"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to preload session {session_id}: {e}")
            return False

    @staticmethod
    def _session_file_path(repo_name: str, session_id: str) -> Path:
        from core.config import get_wiki_chat_dir

        return get_wiki_chat_dir() / repo_name / ".sessions" / f"{session_id}.state.json"

    @staticmethod
    def _restore_from_state_file(
        state_file: Path, session_state: SessionState
    ) -> bool:
        try:
            with open(state_file, encoding="utf-8") as f:
                data = json.load(f)

            serialized_messages = data.get("messages")
            if serialized_messages:
                session_state.messages = messages_from_dict(serialized_messages)

            explored_nodes = data.get("explored_nodes")
            if isinstance(explored_nodes, list):
                session_state.explored_nodes = explored_nodes

            session_state.total_tool_calls = int(data.get("total_tool_calls", 0))
            session_state.extraction_round = int(data.get("extraction_round", 0))
            extraction_summaries = data.get("extraction_summaries")
            if isinstance(extraction_summaries, list):
                session_state.extraction_summaries = extraction_summaries

            created_at = data.get("created_at")
            if created_at:
                session_state.created_at = datetime.fromisoformat(created_at).timestamp()
            last_active = data.get("last_active")
            if last_active:
                session_state.last_active = datetime.fromisoformat(last_active).timestamp()

            return bool(session_state.messages or session_state.explored_nodes)
        except Exception as e:
            logger.warning(f"Failed to restore session state from {state_file}: {e}")
            return False

    @staticmethod
    def _reconstruct_from_turns(
        chat_log: dict[str, Any],
        session_state: SessionState,
        repo_path_obj: Path | None = None,
    ) -> None:
        """Reconstruct session state from saved chat log turns."""
        turns = chat_log.get("turns", [])
        if not turns:
            logger.warning("No turns found in chat log")
            return

        for turn in turns:
            user_msg = HumanMessage(content=turn.get("query", ""))
            session_state.add_messages([user_msg])

            response = turn.get("response", "")
            assistant_msg = AIMessage(content=response)
            session_state.add_messages([assistant_msg])

            references = turn.get("references", [])
            for ref in references:
                if ref.get("path") and ref.get("start_line") and ref.get("end_line"):
                    node = {
                        "qualified_name": ref.get("qualified_name", ""),
                        "name": ref.get("name", ""),
                        "type": ref.get("type", "Unknown"),
                        "path": ref.get("path"),
                        "start_line": ref.get("start_line"),
                        "end_line": ref.get("end_line"),
                        "_repo_path": str(repo_path_obj) if repo_path_obj else None,
                    }
                    session_state.add_explored_nodes([node], repo_path_obj)

            session_state.total_tool_calls += turn.get("tool_calls", 0)
