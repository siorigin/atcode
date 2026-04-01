'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { Suspense } from 'react';
import PapersContent from './PapersContent';

export interface PaperContext {
  title: string;
  paperId: string;
  abstract?: string;
  authors?: string[];
  aiSummary?: string;
}

// Papers page renders PapersContent directly — no outer PanelWorkspace wrapper.
// When viewing a paper detail, PaperWorkspace provides its own docking layout.
export default function PapersLayout() {
  return (
    <div style={{ height: '100%', overflow: 'hidden' }}>
      <Suspense fallback={<div style={{ padding: 24, color: '#888' }}>Loading...</div>}>
        <PapersContent />
      </Suspense>
    </div>
  );
}
