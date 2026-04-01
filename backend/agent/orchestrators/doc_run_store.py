# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict


_MESSAGE_STATE_KEYS = {"messages", "inherited_raw_messages"}


def _json_safe(value: Any) -> Any:
    """Convert arbitrary values into JSON-serializable structures."""
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def serialize_doc_state(state: dict[str, Any]) -> dict[str, Any]:
    """Serialize DocAgentState for local checkpoint persistence."""
    serialized: dict[str, Any] = {}
    for key, value in state.items():
        if key in _MESSAGE_STATE_KEYS and isinstance(value, list):
            base_messages = [msg for msg in value if isinstance(msg, BaseMessage)]
            serialized[key] = messages_to_dict(base_messages)
        else:
            serialized[key] = _json_safe(value)
    return serialized


def deserialize_doc_state(state: dict[str, Any]) -> dict[str, Any]:
    """Restore DocAgentState from a serialized checkpoint payload."""
    restored: dict[str, Any] = {}
    for key, value in state.items():
        if key in _MESSAGE_STATE_KEYS and isinstance(value, list):
            restored[key] = messages_from_dict(value)
        else:
            restored[key] = value
    return restored


class DocRunStore:
    """Local on-disk artifacts for documentation generation runs."""

    def __init__(self, wiki_doc_root: Path, repo_name: str, task_id: str) -> None:
        self.repo_name = repo_name
        self.task_id = task_id
        self.run_dir = wiki_doc_root / repo_name / ".task_runs" / task_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def request_path(self) -> Path:
        return self.run_dir / "request.json"

    @property
    def status_path(self) -> Path:
        return self.run_dir / "status.json"

    @property
    def checkpoint_path(self) -> Path:
        return self.run_dir / "checkpoint.latest.json"

    @property
    def trajectory_path(self) -> Path:
        return self.run_dir / "trajectory.jsonl"

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def save_request(self, payload: dict[str, Any]) -> None:
        data = {
            "saved_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self._write_json_atomic(self.request_path, _json_safe(data))

    def load_request(self) -> dict[str, Any] | None:
        try:
            if not self.request_path.exists():
                return None
            return json.loads(self.request_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_status(self, payload: dict[str, Any]) -> None:
        data = {
            "saved_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self._write_json_atomic(self.status_path, _json_safe(data))

    def append_trajectory_event(self, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            **_json_safe(payload),
        }
        with open(self.trajectory_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_checkpoint(
        self,
        state: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "saved_at": datetime.now(UTC).isoformat(),
            "repo_name": self.repo_name,
            "task_id": self.task_id,
            "metadata": _json_safe(metadata or {}),
            "state": serialize_doc_state(state),
        }
        self._write_json_atomic(self.checkpoint_path, payload)

    def load_checkpoint(self) -> dict[str, Any] | None:
        try:
            if not self.checkpoint_path.exists():
                return None
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            state = payload.get("state")
            if not isinstance(state, dict):
                return None
            return {
                "saved_at": payload.get("saved_at"),
                "repo_name": payload.get("repo_name"),
                "task_id": payload.get("task_id"),
                "metadata": payload.get("metadata") or {},
                "state": deserialize_doc_state(state),
            }
        except Exception:
            return None
