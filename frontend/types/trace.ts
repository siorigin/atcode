// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unified trace types and adapters for chat, doc generation, and background tasks.
 */

import type { ToolTraceItem } from '@/lib/store';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TraceNodeType = 'tool' | 'phase' | 'agent';
export type TraceNodeStatus = 'pending' | 'running' | 'success' | 'error';

export interface TraceNode {
  id: string;
  type: TraceNodeType;
  name: string;
  status: TraceNodeStatus;
  timestamp?: string;
  duration?: number;
  input?: string;
  output?: string;
  preview?: string[];
  error?: string;
  tokens?: number;
  children?: TraceNode[];
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function summarizeTrajectoryDetails(details?: Record<string, unknown> | null): string {
  if (!details) return '';
  const parts: string[] = [];
  const toolCalls = details.tool_call_count;
  const exploredNodes = details.explored_node_count;
  const outlineCount = details.outline_count;
  const completedSections = details.completed_section_count;
  if (typeof toolCalls === 'number' && toolCalls > 0) parts.push(`${toolCalls} tool calls`);
  if (typeof exploredNodes === 'number' && exploredNodes > 0) parts.push(`${exploredNodes} nodes`);
  if (typeof outlineCount === 'number' && outlineCount > 0) parts.push(`${outlineCount} planned`);
  if (typeof completedSections === 'number' && completedSections > 0) parts.push(`${completedSections} done`);
  return parts.join(' \u2022 ');
}

function extractToolCallNodes(
  details: Record<string, unknown> | null | undefined,
  prefix: string,
): TraceNode[] {
  const recentToolCalls = details?.recent_tool_calls;
  if (!Array.isArray(recentToolCalls)) return [];
  return recentToolCalls
    .filter((call: any) => call?.display)
    .map((call: any, i: number) => ({
      id: `${prefix}-tc${i}`,
      type: 'tool' as const,
      name: call.display,
      status: 'success' as const,
      output: typeof call.result_preview === 'string' ? call.result_preview : undefined,
    }));
}

// ---------------------------------------------------------------------------
// Adapters
// ---------------------------------------------------------------------------

/** Chat completed-message tool trace → TraceNode[] */
export function adaptChatToolTrace(toolTrace: ToolTraceItem[], messageId: string): TraceNode[] {
  return toolTrace.map((tc, idx) => ({
    id: `${messageId}-t${idx}`,
    type: 'tool' as const,
    name: tc.tool || 'Unknown Tool',
    status: (tc.result ? 'success' : 'pending') as TraceNodeStatus,
    input: tc.key_arg || undefined,
    output: tc.result || undefined,
    preview: tc.preview || undefined,
  }));
}

/** Chat streaming tool trace → TraceNode[] (last item may be running) */
export function adaptStreamingToolTrace(
  toolCallHistory: ToolTraceItem[],
  currentToolCall: string | null,
  sessionId: string,
): TraceNode[] {
  return toolCallHistory.map((tc, idx) => {
    const isLast = idx === toolCallHistory.length - 1;
    const isActive = isLast && currentToolCall !== null;
    return {
      id: `${sessionId}-stream-t${idx}`,
      type: 'tool' as const,
      name: tc.tool,
      status: (isActive ? 'running' : tc.result ? 'success' : 'pending') as TraceNodeStatus,
      input: tc.key_arg || undefined,
      output: tc.result || undefined,
      preview: tc.preview || undefined,
    };
  });
}

type TrajectoryEvent = {
  timestamp: string;
  status: string;
  progress: number;
  step: string;
  message: string;
  error?: string | null;
  details?: Record<string, unknown> | null;
};

/**
 * Background task trajectory → tree of TraceNode[].
 *
 * Groups events into:
 *   Main Agent (phase node)
 *     └─ tool calls (children)
 *   Child Agent: <scope> (agent node)
 *     └─ tool calls / step changes (children)
 */
export function adaptTaskTrajectory(
  trajectory: TrajectoryEvent[] | undefined,
  taskId: string,
): TraceNode[] {
  if (!trajectory || trajectory.length === 0) return [];

  const roots: TraceNode[] = [];
  const childAgentNodes = new Map<string, TraceNode>();

  for (let idx = 0; idx < trajectory.length; idx++) {
    const event = trajectory[idx];
    const details = event.details;
    const childScope = details?.child_scope as string | undefined;
    const phase = details?.phase as string | undefined;
    const toolCalls = extractToolCallNodes(details, `${taskId}-e${idx}`);
    const isLast = idx === trajectory.length - 1;

    // Past events are always "success"; only the very last event
    // may still be running (if the task itself is still active).
    const nodeStatus: TraceNodeStatus =
      event.status === 'failed' ? 'error' :
      isLast && event.status === 'running' ? 'running' :
      'success';

    if (phase === 'child_working' && childScope) {
      // Child agent event — group under a single agent node per scope
      let agentNode = childAgentNodes.get(childScope);
      if (!agentNode) {
        agentNode = {
          id: `${taskId}-child-${childScope}`,
          type: 'agent',
          name: childScope,
          status: 'success',
          timestamp: event.timestamp,
          children: [],
        };
        childAgentNodes.set(childScope, agentNode);
        roots.push(agentNode);
      }

      // Update agent status — only last event in the whole trajectory can be running
      agentNode.status = nodeStatus;

      const activityName = event.message.replace(`[${childScope}] `, '');
      if (toolCalls.length > 0) {
        for (const tc of toolCalls) {
          agentNode.children!.push(tc);
        }
      } else if (activityName) {
        agentNode.children!.push({
          id: `${taskId}-e${idx}`,
          type: 'phase',
          name: activityName,
          status: nodeStatus,
          timestamp: event.timestamp,
        });
      }
    } else {
      // Main agent event
      const node: TraceNode = {
        id: `${taskId}-e${idx}`,
        type: 'phase',
        name: event.message || event.step || event.status,
        status: nodeStatus,
        timestamp: event.timestamp,
        input: summarizeTrajectoryDetails(details) || undefined,
        error: event.error || undefined,
        children: toolCalls.length > 0 ? toolCalls : undefined,
        metadata: details || undefined,
      };
      roots.push(node);
    }
  }

  return roots;
}
