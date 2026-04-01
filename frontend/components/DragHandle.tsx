'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';

interface DragHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
  position: 'left' | 'right';
}

export function DragHandle({ onMouseDown, position }: DragHandleProps) {
  const positionStyle = position === 'left' ? { left: '-6px' } : { right: '-6px' };
  
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        bottom: 0,
        ...positionStyle,
        width: '12px',
        cursor: 'col-resize',
        zIndex: 10,
        backgroundColor: 'transparent',
        transition: 'background-color 200ms',
      }}
      onMouseDown={onMouseDown}
      onMouseEnter={(e) => {
        e.currentTarget.style.backgroundColor = 'rgba(96, 165, 250, 0.3)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.backgroundColor = 'transparent';
      }}
      title="Drag to resize columns"
    />
  );
}
