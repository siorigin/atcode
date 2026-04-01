'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * GlobalTaskMonitor - Client-side wrapper for TaskStatusPanel
 *
 * This component wraps TaskStatusPanel for use in server component layouts.
 * It handles client-side mounting and provides the global task monitoring UI.
 */

import React from 'react';
import { TaskStatusPanel } from './TaskStatusPanel';

export interface GlobalTaskMonitorProps {
  /**
   * Position of the panel.
   * Default: 'bottom-right'
   */
  position?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
}

export function GlobalTaskMonitor({
  position = 'bottom-right',
}: GlobalTaskMonitorProps) {
  return (
    <TaskStatusPanel
      position={position}
    />
  );
}

export default GlobalTaskMonitor;
