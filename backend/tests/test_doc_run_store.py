# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from langchain_core.messages import HumanMessage

from agent.orchestrators.doc_run_store import DocRunStore


def test_doc_run_store_round_trips_checkpoint_state(tmp_path):
    store = DocRunStore(tmp_path, "demo-repo", "task-123")

    state = {
        "repo_name": "demo-repo",
        "messages": [HumanMessage(content="hello checkpoint")],
        "inherited_raw_messages": [HumanMessage(content="parent context")],
        "outline": [{"title": "Architecture", "description": "System design", "order": 1}],
        "child_results": [{"title": "Architecture", "order": 1, "child_results": []}],
        "tool_call_count": 3,
    }

    store.save_request({"repo": "demo-repo", "doc_depth": 2})
    store.append_trajectory_event({"task_status": "running", "step": "tool_call"})
    store.save_checkpoint(state, metadata={"doc_depth": 2})

    request = store.load_request()
    checkpoint = store.load_checkpoint()

    assert request is not None
    assert request["repo"] == "demo-repo"
    assert checkpoint is not None
    assert checkpoint["metadata"]["doc_depth"] == 2
    restored_state = checkpoint["state"]
    assert restored_state["repo_name"] == "demo-repo"
    assert restored_state["messages"][0].content == "hello checkpoint"
    assert restored_state["inherited_raw_messages"][0].content == "parent context"
    assert restored_state["outline"][0]["title"] == "Architecture"
    assert store.trajectory_path.exists()
