# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from loguru import logger

# ============================================================================
# CONFIGURATION
# ============================================================================


@dataclass
class ContextExtractionConfig:
    """Configuration for context extraction behavior.

    Attributes:
        tool_call_threshold: Trigger extraction after this many tool calls
        keep_recent_messages: Keep the most recent N tool messages uncompressed
        messages_per_round: Process N old tool messages per extraction round
        auto_keep_token_threshold: Small outputs (< this) auto-kept (extraction cost > keeping)
        scope_title: Title of current scope (for prompt context)
        scope_description: Description of current scope (for prompt context)
    """

    tool_call_threshold: int = 20
    keep_recent_messages: int = 10
    messages_per_round: int = 10
    auto_keep_token_threshold: int = 300
    scope_title: str = ""
    scope_description: str = ""

    # Optional: Custom classification labels
    classification_labels: dict[str, str] = field(
        default_factory=lambda: {
            "keep_full": "KEEP_FULL",
            "extract_info": "EXTRACT_INFO",
            "minimal_record": "MINIMAL_RECORD",
        }
    )


# ============================================================================
# DEFAULT CONFIGURATION
# ============================================================================

DEFAULT_CONFIG = ContextExtractionConfig()


# ============================================================================
# PROMPT BUILDING
# ============================================================================


def build_extraction_evaluation_prompt(
    msgs_to_process: list[tuple[int, Any]],
    scope_title: str,
    scope_description: str,
    depth: int = 0,
    extraction_round: int = 0,
    previous_summaries: list[str] | None = None,
    task_context: str = "documentation",
) -> str:
    """
    Build the prompt for LLM to evaluate tool outputs and decide extraction strategy.

    This prompt asks the LLM to classify each tool output as:
    - KEEP_FULL: Core implementation code directly relevant to the task
    - EXTRACT_INFO: Extract key information, discard raw output
    - MINIMAL_RECORD: Just note what was explored

    Args:
        msgs_to_process: List of (global_index, ToolMessage) tuples to evaluate
        scope_title: Title of current scope/task
        scope_description: Description of what this task covers
        depth: Current depth level (for nested workflows)
        extraction_round: Which extraction round this is
        previous_summaries: Summaries from previous extraction rounds
        task_context: Type of task ("documentation", "research", "chat")

    Returns:
        Formatted prompt string for LLM evaluation
    """
    previous_summaries = previous_summaries or []

    # Prepare tool outputs for evaluation
    tool_outputs = []
    for local_idx, (global_idx, msg) in enumerate(msgs_to_process):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        tool_call_id = getattr(msg, "tool_call_id", "unknown")

        # Estimate token count (rough: 1 token ≈ 4 chars)
        estimated_tokens = len(content) // 4

        tool_outputs.append(
            {
                "index": local_idx,
                "tool_call_id": tool_call_id,
                "content": content,
                "estimated_tokens": estimated_tokens,
            }
        )

    prompt = f"""You are reviewing your previous tool exploration results for {task_context}.

**Primary Objective:**
- Preserve useful information with the lowest possible token cost
- Do NOT optimize for summarizing everything
- Do NOT optimize for keeping everything
- Choose the shortest representation that preserves all information likely to matter later

**Current Task Context:**
- Scope: {scope_title}
- Description: {scope_description}
- Depth Level: {depth}
- Extraction Round: {extraction_round + 1}

"""
    if previous_summaries:
        prompt += "**Previous Extraction Summaries:**\n"
        for i, summary in enumerate(previous_summaries[-2:], 1):
            truncated = summary[:300] + "..." if len(summary) > 300 else summary
            prompt += f"Round {i}: {truncated}\n"
        prompt += "\n"

    prompt += f"""**Task:** Review the following {len(tool_outputs)} tool results and classify each one.

**Classification Options:**
- **KEEP_FULL**: Keep the full content.
  Use this when:
  * Most of the content is useful, dense, and likely reusable
  * Compressing it would lose important detail
  * Extracting it would save little token cost compared with the raw output

- **EXTRACT_INFO**: Keep only the reusable facts and discard the raw output.
  Use this when:
  * Only part of the result is useful for later reasoning
  * The useful information can be preserved as a short structured note
  * You can keep the key facts while removing surrounding noise
  * This is NOT a general summary

- **MINIMAL_RECORD**: Keep only a breadcrumb of exploration.
  Use this when:
  * The result has little or no reusable value
  * You only need to remember what was checked
  * Record what was checked and why this branch can be deprioritized

**Tool Results to Evaluate:**

"""
    for item in tool_outputs:
        prompt += f"""
--- Tool Result [{item["index"]}] (≈{item["estimated_tokens"]} tokens) ---
{item["content"]}
"""

    prompt += """

**Response Format (JSON):**
```json
{
  "decisions": [
    {
      "index": 0,
      "decision": "KEEP_FULL" | "EXTRACT_INFO" | "MINIMAL_RECORD",
      "extracted_info": "For EXTRACT_INFO/MINIMAL_RECORD: the shortest useful record to keep. For KEEP_FULL use an empty string."
    },
    ...
  ],
  "exploration_summary": "Optional very short summary of overall exploration progress (prefer <= 30 tokens)"
}
```

**Guidelines:**
1. Think in terms of information density, not tool type
2. If most of the content is useful information, choose KEEP_FULL
3. If the useful information is small relative to the raw output, choose EXTRACT_INFO
4. If there is almost no reusable information, choose MINIMAL_RECORD
5. KEEP_FULL is acceptable when extraction would save little or would lose fidelity
6. For EXTRACT_INFO, preserve only reusable facts: qualified names, file paths, 1-3 key relationships/patterns, and at most one short conclusion
7. EXTRACT_INFO should usually stay within 80-150 tokens, and only go up to 200 tokens for unusually dense results
8. MINIMAL_RECORD should be a breadcrumb only: one short sentence, ideally <= 30 tokens
9. Preserve exact identifiers, file paths, and concrete relationships when they matter
10. Do not write long prose in extracted_info or exploration_summary

Please provide your evaluation:"""

    return prompt


def parse_extraction_decisions(
    response_content: str,
    expected_count: int,
) -> dict:
    """
    Parse the LLM's extraction decisions from JSON response.

    Args:
        response_content: Raw LLM response text
        expected_count: Number of decisions expected

    Returns:
        Dict with 'decisions' list and 'exploration_summary' string
    """
    # Try to extract JSON from response
    try:
        # Look for JSON block
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", response_content)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r'\{[\s\S]*"decisions"[\s\S]*\}', response_content)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError("No JSON found in response")

        decisions = json.loads(json_str)

        # Validate structure
        if "decisions" not in decisions:
            decisions = {"decisions": [], "exploration_summary": ""}

        # Ensure we have decisions for all messages
        while len(decisions["decisions"]) < expected_count:
            decisions["decisions"].append(
                {
                    "index": len(decisions["decisions"]),
                    "decision": "EXTRACT_INFO",
                    "extracted_info": "Tool exploration result",
                }
            )

        return decisions

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse extraction decisions: {e}")
        # Return default decisions
        return {
            "decisions": [
                {
                    "index": i,
                    "decision": "EXTRACT_INFO",
                    "extracted_info": f"Tool result {i + 1}",
                }
                for i in range(expected_count)
            ],
            "exploration_summary": "Extraction parsing failed, using defaults.",
        }


def build_condensed_context(
    messages: list[BaseMessage],
    scope_title: str,
    scope_description: str,
    depth: int = 0,
) -> str:
    """
    Build a condensed context for extraction LLM call.

    Instead of sending the full message history (expensive), we extract:
    1. Scope information
    2. Key exploration decisions from AIMessages (what the agent was looking for)
    3. Tool calls made (what tools were called and with what parameters)
    4. Previously extracted summaries

    This significantly reduces input tokens while preserving decision context.

    Args:
        messages: Full message history
        scope_title: Title of current scope
        scope_description: Description of current scope
        depth: Current depth level

    Returns:
        Condensed context string
    """
    context_parts = [
        "You are an assistant helping compress code exploration results while preserving useful information.",
        "",
        "## Current Task Scope",
        f"- Title: {scope_title}",
        f"- Description: {scope_description}",
        f"- Depth Level: {depth}",
        "",
        "## Exploration Context",
        "The following summarizes the exploration journey so far:",
        "",
    ]

    # Build a map from tool_call_id to ToolMessage content for result lookup
    tool_results_map = {}
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                tool_results_map[tool_call_id] = content

    # Extract key decisions/thoughts from AI messages AND their tool calls (last 5)
    ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
    recent_ai = ai_messages[-5:] if len(ai_messages) > 5 else ai_messages

    for i, msg in enumerate(recent_ai):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        # Truncate long AI responses to key parts
        if len(content) > 400:
            content = content[:400] + "..."
        context_parts.append(f"[Agent thought {i + 1}]: {content}")

        # Also include tool calls AND their results for context
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_summaries = []
            for tc in msg.tool_calls[:10]:  # Limit to 10 tool calls per message
                if isinstance(tc, dict):
                    tool_name = tc.get("name", "unknown")
                    tool_args = tc.get("args", {})
                    tool_call_id = tc.get("id", None)
                else:
                    tool_name = getattr(tc, "name", "unknown")
                    tool_args = getattr(tc, "args", {})
                    tool_call_id = getattr(tc, "id", None)

                # Summarize args (keep it short)
                args_summary = []
                for k, v in list(tool_args.items())[:3]:
                    v_str = str(v)[:50] + "..." if len(str(v)) > 50 else str(v)
                    args_summary.append(f"{k}={v_str}")
                args_str = ", ".join(args_summary) if args_summary else ""
                tool_summaries.append(f"  - Called `{tool_name}({args_str})`")

                # Add truncated result if available
                if tool_call_id and tool_call_id in tool_results_map:
                    result = tool_results_map[tool_call_id]
                    # Truncate result to first 200 chars for context
                    if len(result) > 200:
                        result = result[:200] + "..."
                    tool_summaries.append(f"    → Result: {result}")

            if tool_summaries:
                context_parts.extend(tool_summaries)

    context_parts.append("")
    context_parts.append("## Your Task")
    context_parts.append(
        "Choose the shortest representation that preserves useful information likely to matter later."
    )
    context_parts.append(
        "If most of a result is useful, keep it full. If only a little is useful, extract only that. If it has little reusable value, leave only a minimal breadcrumb."
    )
    context_parts.append(
        "For extracted records, focus on exact identifiers, file paths, and concrete relationships. Keep them short."
    )
    context_parts.append(
        "Use the tool call context above to understand what the agent was searching for."
    )

    return "\n".join(context_parts)


# ============================================================================
# EXTRACTION LOGIC
# ============================================================================


def should_trigger_extraction(
    tool_call_count: int,
    extraction_round: int,
    config: ContextExtractionConfig | None = None,
) -> bool:
    """
    Determine if context extraction should be triggered.

    Args:
        tool_call_count: Current total tool call count
        extraction_round: Current extraction round number
        config: Extraction configuration (uses defaults if None)

    Returns:
        True if extraction should be triggered
    """
    config = config or DEFAULT_CONFIG
    expected_round = tool_call_count // config.tool_call_threshold
    return expected_round > extraction_round


def identify_messages_to_extract(
    messages: list[BaseMessage],
    config: ContextExtractionConfig | None = None,
) -> tuple[list[tuple[int, ToolMessage]], list[int], list[tuple[int, ToolMessage]]]:
    """
    Identify which tool messages need extraction processing.

    Returns:
        Tuple of:
        - msgs_to_process: [(global_idx, ToolMessage), ...] messages needing extraction
        - auto_keep_indices: Set of global indices for small outputs (auto-keep)
        - needs_extraction_list: [(global_idx, ToolMessage), ...] for LLM processing
    """
    config = config or DEFAULT_CONFIG

    # Find all ToolMessages with their indices
    tool_messages = [
        (i, msg) for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)
    ]

    if len(tool_messages) <= config.keep_recent_messages:
        return [], [], []

    # Keep the most recent messages unchanged
    if config.keep_recent_messages > 0:
        older_tool_msgs = tool_messages[: -config.keep_recent_messages]
    else:
        older_tool_msgs = tool_messages

    # Skip already extracted messages
    unprocessed_older = [
        (idx, msg)
        for idx, msg in older_tool_msgs
        if not (isinstance(msg.content, str) and msg.content.startswith("[Extracted]"))
    ]

    msgs_to_process = unprocessed_older

    if not msgs_to_process:
        return [], [], []

    # Separate small outputs (auto-keep) from larger ones (need LLM extraction)
    auto_keep_indices = []
    needs_extraction_list = []

    for global_idx, msg in msgs_to_process:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        estimated_tokens = len(content) // 4

        if estimated_tokens < config.auto_keep_token_threshold:
            auto_keep_indices.append(global_idx)
        else:
            needs_extraction_list.append((global_idx, msg))

    return msgs_to_process, auto_keep_indices, needs_extraction_list


def apply_extraction_decisions(
    msgs_to_process: list[tuple[int, ToolMessage]],
    auto_keep_indices: list[int],
    llm_decisions: dict[int, dict],
) -> list[ToolMessage]:
    """
    Apply extraction decisions and create replacement messages.

    Uses same-ID replacement strategy for add_messages reducer.

    Args:
        msgs_to_process: All messages being processed
        auto_keep_indices: Indices of messages to auto-keep
        llm_decisions: Dict mapping global_idx -> decision_info

    Returns:
        List of ToolMessage replacements (with same IDs for in-place replacement)
    """
    message_updates = []
    kept_full_count = 0
    extracted_count = 0

    for global_idx, msg in msgs_to_process:
        # Check if it's an auto-keep (small output)
        if global_idx in auto_keep_indices:
            kept_full_count += 1
            continue

        # Check LLM decision
        decision_info = llm_decisions.get(global_idx)
        if decision_info is None:
            kept_full_count += 1
            continue

        decision = decision_info.get("decision", "EXTRACT_INFO")

        if decision == "KEEP_FULL":
            kept_full_count += 1
        else:
            # EXTRACT_INFO or MINIMAL_RECORD: replace with compressed version
            extracted_info = decision_info.get(
                "extracted_info", "Tool exploration result"
            )
            original_tool_call_id = getattr(msg, "tool_call_id", "unknown")
            compressed_content = f"[Extracted] {extracted_info}"

            # Get the message ID for in-place replacement
            msg_id = getattr(msg, "id", None)

            if msg_id:
                # Create new ToolMessage with SAME ID for in-place replacement
                message_updates.append(
                    ToolMessage(
                        content=compressed_content,
                        tool_call_id=original_tool_call_id,
                        id=msg_id,
                    )
                )
                extracted_count += 1
            else:
                logger.warning(
                    f"ToolMessage at index {global_idx} has no ID, keeping original"
                )
                kept_full_count += 1

    logger.info(
        f"Extraction: kept_full={kept_full_count}, compressed={extracted_count}"
    )
    return message_updates


# ============================================================================
# NODE FACTORIES
# ============================================================================


def create_extract_context_node(
    llm: Any,
    config: ContextExtractionConfig | None = None,
    task_context: str = "research",
) -> Callable:
    """
    Factory function to create an extract_context_node for a LangGraph workflow.

    The returned node function can be added to any StateGraph that has:
    - messages: list[BaseMessage] with add_messages reducer
    - tool_call_count: int tracking total tool calls
    - extraction_round: int tracking extraction rounds
    - extraction_summaries: list[str] for accumulating summaries
    - need_extraction: bool flag to trigger extraction

    Args:
        llm: LangChain LLM instance for extraction evaluation
        config: Extraction configuration
        task_context: Type of task for prompt context

    Returns:
        Async node function compatible with LangGraph StateGraph

    Example:
        ```python
        from .context_extraction import create_extract_context_node, ContextExtractionConfig

        config = ContextExtractionConfig(
            tool_call_threshold=15,
            scope_title="Code Research",
        )
        extract_node = create_extract_context_node(llm, config, task_context="research")

        graph.add_node("extract_context", extract_node)
        ```
    """
    config = config or DEFAULT_CONFIG

    async def extract_context_node(state: dict) -> dict:
        """
        Extract key insights from accumulated tool messages using hybrid approach.

        This node:
        1. Identifies old tool messages that need compression
        2. Auto-keeps small outputs (not worth extracting)
        3. Uses LLM to evaluate larger outputs and decide extraction strategy
        4. Creates replacement messages with same IDs for in-place update
        """
        messages = list(state.get("messages", []))
        extraction_round = state.get("extraction_round", 0)
        previous_summaries = state.get("extraction_summaries", [])

        # Get scope from state or config
        scope_title = state.get("scope_title", config.scope_title) or "Current Task"
        scope_description = (
            state.get("scope_description", config.scope_description) or ""
        )
        depth = state.get("current_depth", 0)

        logger.info(
            f"Extract context: scope='{scope_title}', round={extraction_round + 1}"
        )

        # Identify messages to process
        msgs_to_process, auto_keep_indices, needs_extraction_list = (
            identify_messages_to_extract(messages, config)
        )

        if not msgs_to_process:
            logger.info("No tool messages to process, skipping extraction")
            return {
                "need_extraction": False,
                "extraction_round": extraction_round + 1,
            }

        logger.info(
            f"Pre-filtering: {len(auto_keep_indices)} auto-keep, "
            f"{len(needs_extraction_list)} need extraction"
        )

        # LLM extraction for larger outputs
        llm_decisions = {}
        exploration_summary = ""

        if needs_extraction_list:
            # Build evaluation prompt
            evaluation_prompt = build_extraction_evaluation_prompt(
                msgs_to_process=needs_extraction_list,
                scope_title=scope_title,
                scope_description=scope_description,
                depth=depth,
                extraction_round=extraction_round,
                previous_summaries=previous_summaries,
                task_context=task_context,
            )

            # Build condensed context
            condensed_context = build_condensed_context(
                messages=messages,
                scope_title=scope_title,
                scope_description=scope_description,
                depth=depth,
            )

            eval_messages = [
                SystemMessage(content=condensed_context),
                HumanMessage(content=evaluation_prompt),
            ]

            try:
                eval_response = await llm.ainvoke(eval_messages)
                eval_content = (
                    eval_response.content
                    if hasattr(eval_response, "content")
                    else str(eval_response)
                )
                parsed = parse_extraction_decisions(
                    eval_content, len(needs_extraction_list)
                )

                # Map decisions back using global_idx
                for i, (global_idx, msg) in enumerate(needs_extraction_list):
                    llm_decisions[global_idx] = parsed["decisions"][i]

                exploration_summary = parsed.get("exploration_summary", "")

            except Exception as e:
                logger.warning(f"Failed to get extraction decisions: {e}")
                for global_idx, msg in needs_extraction_list:
                    llm_decisions[global_idx] = {
                        "decision": "EXTRACT_INFO",
                        "extracted_info": "Tool exploration result (fallback)",
                    }
                exploration_summary = (
                    f"Explored {len(needs_extraction_list)} code locations."
                )
        else:
            exploration_summary = (
                f"Processed {len(auto_keep_indices)} small tool outputs (auto-kept)."
            )

        # Apply decisions and create replacements
        message_updates = apply_extraction_decisions(
            msgs_to_process, auto_keep_indices, llm_decisions
        )

        return {
            "messages": message_updates,
            "need_extraction": False,
            "extraction_round": extraction_round + 1,
            "extraction_summaries": [exploration_summary]
            if exploration_summary
            else [],
        }

    return extract_context_node
