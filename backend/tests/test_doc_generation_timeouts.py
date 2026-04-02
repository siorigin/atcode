# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
from pathlib import Path

from api.routes import overview
from api.services.task_queue import TaskStatus

_SHARED_PATH = (
    Path(__file__).resolve().parents[1] / "agent" / "orchestrators" / "shared.py"
)
_SHARED_SPEC = importlib.util.spec_from_file_location("test_shared_module", _SHARED_PATH)
assert _SHARED_SPEC is not None and _SHARED_SPEC.loader is not None
_shared = importlib.util.module_from_spec(_SHARED_SPEC)
_SHARED_SPEC.loader.exec_module(_shared)
_try_invoke = _shared._try_invoke


class _TimeoutThenSuccessLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.05)
        return {"messages": messages, "attempt": self.calls}


def test_try_invoke_retries_after_timeout(monkeypatch):
    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_SECONDS", "0.01")

    llm = _TimeoutThenSuccessLLM()
    result = asyncio.run(
        _try_invoke(
            llm,
            ["hello"],
            label="doc-test",
            max_retries=2,
            retry_delays=[0],
        )
    )

    assert result["attempt"] == 2
    assert llm.calls == 2


class _FakeTaskManager:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.unregistered: list[str] = []

    async def get_task_status(self, task_id: str):
        return {"task_id": task_id}

    async def update_task(self, task_id: str, **kwargs):
        self.updates.append({"task_id": task_id, **kwargs})

    def unregister_task(self, task_id: str) -> None:
        self.unregistered.append(task_id)


class _StallingOrchestrator:
    async def stream_generate(self, **kwargs):
        yield {
            "type": "status",
            "content": "Child agents generating sections...",
            "progress": 65,
            "step": "children_working",
            "details": {"phase": "dispatching"},
        }
        await asyncio.sleep(0.05)


def test_generate_overview_background_marks_task_stalled_when_progress_stalls(monkeypatch, tmp_path):
    monkeypatch.setenv("OVERVIEW_PROGRESS_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("OVERVIEW_AUTO_RESUME_ON_STALL", "false")

    task_manager = _FakeTaskManager()
    monkeypatch.setattr(overview, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(overview, "get_memgraph_ingestor", lambda: object())
    monkeypatch.setattr(
        overview, "get_overview_orchestrator", lambda **kwargs: _StallingOrchestrator()
    )
    monkeypatch.setattr(overview, "get_wiki_doc_path", lambda: tmp_path)

    asyncio.run(
        overview._generate_overview_background(
            task_id="task-1",
            repo="demo-repo",
        )
    )

    assert task_manager.unregistered == ["task-1"]
    assert len(task_manager.updates) >= 2
    assert task_manager.updates[-1]["status"] == TaskStatus.STALLED
    assert "stalled for" in (task_manager.updates[-1]["error"] or "")
    assert "children_working" in (task_manager.updates[-1]["error"] or "")


def test_generate_overview_background_emits_heartbeat_before_stalling(monkeypatch, tmp_path):
    monkeypatch.setenv("OVERVIEW_PROGRESS_TIMEOUT_SECONDS", "0.03")
    monkeypatch.setenv("OVERVIEW_PROGRESS_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("OVERVIEW_AUTO_RESUME_ON_STALL", "false")

    task_manager = _FakeTaskManager()
    monkeypatch.setattr(overview, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(overview, "get_memgraph_ingestor", lambda: object())
    monkeypatch.setattr(
        overview, "get_overview_orchestrator", lambda **kwargs: _StallingOrchestrator()
    )
    monkeypatch.setattr(overview, "get_wiki_doc_path", lambda: tmp_path)

    asyncio.run(
        overview._generate_overview_background(
            task_id="task-heartbeat",
            repo="demo-repo",
        )
    )

    heartbeat_updates = [
        update
        for update in task_manager.updates
        if update.get("details", {}).get("heartbeat") is True
    ]

    assert heartbeat_updates
    assert task_manager.updates[-1]["status"] == TaskStatus.STALLED


class _ReentrySensitiveStream:
    def __init__(self) -> None:
        self.calls = 0
        self.inflight = False
        self._release_tasks: list[asyncio.Task] = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.calls += 1
        if self.calls == 1:
            return {
                "type": "status",
                "content": "Child agents generating sections...",
                "progress": 65,
                "step": "children_working",
                "details": {"phase": "dispatching"},
            }

        if self.inflight:
            raise RuntimeError("anext(): asynchronous generator is already running")

        self.inflight = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            async def _release() -> None:
                await asyncio.sleep(0.05)
                self.inflight = False

            self._release_tasks.append(asyncio.create_task(_release()))
            raise

        self.inflight = False
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.inflight = False
        for task in self._release_tasks:
            task.cancel()
        for task in self._release_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


class _ReentrySensitiveOrchestrator:
    def stream_generate(self, **kwargs):
        return _ReentrySensitiveStream()


def test_generate_overview_background_reuses_pending_next_during_heartbeats(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OVERVIEW_PROGRESS_TIMEOUT_SECONDS", "0.03")
    monkeypatch.setenv("OVERVIEW_PROGRESS_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("OVERVIEW_AUTO_RESUME_ON_STALL", "false")

    task_manager = _FakeTaskManager()
    monkeypatch.setattr(overview, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(overview, "get_memgraph_ingestor", lambda: object())
    monkeypatch.setattr(
        overview,
        "get_overview_orchestrator",
        lambda **kwargs: _ReentrySensitiveOrchestrator(),
    )
    monkeypatch.setattr(overview, "get_wiki_doc_path", lambda: tmp_path)

    asyncio.run(
        overview._generate_overview_background(
            task_id="task-reentry-safe",
            repo="demo-repo",
        )
    )

    heartbeat_updates = [
        update
        for update in task_manager.updates
        if update.get("details", {}).get("heartbeat") is True
    ]

    assert len(heartbeat_updates) >= 2
    assert not any(
        update.get("status") == TaskStatus.FAILED for update in task_manager.updates
    )
    assert task_manager.updates[-1]["status"] == TaskStatus.STALLED


class _FakeRunStore:
    def __init__(self) -> None:
        self.request: dict | None = None
        self.status: dict | None = None
        self.trajectory: list[dict] = []
        self.checkpoint: dict | None = {
            "state": {
                "outline": [
                    {
                        "title": "Section A",
                        "description": "Recovered section",
                        "order": 1,
                    }
                ],
                "should_delegate": True,
                "child_results": [],
            }
        }

    def save_request(self, payload: dict) -> None:
        self.request = payload

    def load_request(self) -> dict | None:
        return self.request

    def save_status(self, payload: dict) -> None:
        self.status = payload

    def append_trajectory_event(self, payload: dict) -> None:
        self.trajectory.append(payload)

    def save_checkpoint(self, state: dict, metadata: dict | None = None) -> None:
        self.checkpoint = {"state": state, "metadata": metadata or {}}

    def load_checkpoint(self) -> dict | None:
        return self.checkpoint


class _AutoResumingOrchestrator:
    def __init__(self) -> None:
        self.resume_calls = 0

    async def stream_generate(self, **kwargs):
        yield {
            "type": "status",
            "content": "Child agents generating sections...",
            "progress": 65,
            "step": "children_working",
            "details": {
                "phase": "dispatching",
                "outline_count": 1,
                "completed_section_count": 0,
            },
        }
        await asyncio.sleep(0.05)

    async def stream_resume(self, **kwargs):
        self.resume_calls += 1
        yield {
            "type": "status",
            "content": "Recovered section Section A (1/1)",
            "progress": 90,
            "step": "resumed_section_complete",
            "details": {
                "phase": "resumed_section_complete",
                "outline_count": 1,
                "completed_section_count": 1,
                "current_section_title": "Section A",
            },
        }
        yield {
            "type": "complete",
            "content": {
                "statistics": {
                    "sections_generated": 1,
                    "total_files": 1,
                    "max_depth_reached": 1,
                }
            },
            "progress": 100,
            "step": "complete",
            "details": {"phase": "complete"},
        }


def test_generate_overview_background_auto_resumes_missing_sections(monkeypatch, tmp_path):
    monkeypatch.setenv("OVERVIEW_PROGRESS_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("OVERVIEW_PROGRESS_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("OVERVIEW_AUTO_RESUME_ON_STALL", "true")
    monkeypatch.setenv("OVERVIEW_AUTO_RESUME_MAX_ATTEMPTS", "1")

    task_manager = _FakeTaskManager()
    run_store = _FakeRunStore()
    orchestrator = _AutoResumingOrchestrator()

    monkeypatch.setattr(overview, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(overview, "get_memgraph_ingestor", lambda: object())
    monkeypatch.setattr(
        overview, "get_overview_orchestrator", lambda **kwargs: orchestrator
    )
    monkeypatch.setattr(overview, "get_wiki_doc_path", lambda: tmp_path)
    monkeypatch.setattr(overview, "get_doc_run_store", lambda repo, task_id: run_store)

    asyncio.run(
        overview._generate_overview_background(
            task_id="task-auto-resume",
            repo="demo-repo",
        )
    )

    assert orchestrator.resume_calls == 1
    assert any(update.get("step") == "auto_resuming" for update in task_manager.updates)
    assert task_manager.updates[-1]["status"] == TaskStatus.COMPLETED
