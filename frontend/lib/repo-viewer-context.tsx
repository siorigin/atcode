'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useState, useCallback, useRef } from 'react';

interface CodeBlock {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  code: string;
  language?: string;
  qualified_name?: string;
}

interface FloatingPanelState {
  isOpen: boolean;
  repoName: string;
  targetQualifiedName?: string;
  // Which tab to activate: 'blocks' for code entity clicks, 'repo' for folder/file clicks
  targetTab?: 'blocks' | 'repo';
}

interface FloatingPanelContextType {
  state: FloatingPanelState;
  codeBlocks: CodeBlock[];
  activeBlockId: string | null;
  // Open floating panel to repo browser (folder/file navigation)
  openRepoViewer: (repoName: string, targetQualifiedName?: string) => void;
  // Open floating panel to code blocks tab with a new code block
  openCodeBlock: (repoName: string, block: CodeBlock) => void;
  // Add a code block without opening (used when panel is already open)
  addCodeBlock: (block: CodeBlock) => void;
  // Activate a code block by file + line range
  activateBlock: (file: string, startLine: number, endLine: number) => void;
  closePanel: () => void;
  clearCodeBlocks: () => void;
}

const FloatingPanelContext = createContext<FloatingPanelContextType | null>(null);

export function RepoViewerProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<FloatingPanelState>({
    isOpen: false,
    repoName: '',
  });
  const [codeBlocks, setCodeBlocks] = useState<CodeBlock[]>([]);
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null);
  const seenBlockIds = useRef(new Set<string>());

  const openRepoViewer = useCallback((repoName: string, targetQualifiedName?: string) => {
    setState(prev => {
      if (prev.isOpen && prev.repoName === repoName) {
        return { ...prev, targetQualifiedName, targetTab: 'repo' as const };
      }
      return { isOpen: true, repoName, targetQualifiedName, targetTab: 'repo' as const };
    });
  }, []);

  const addCodeBlock = useCallback((block: CodeBlock) => {
    setCodeBlocks(prev => {
      if (prev.some(b => b.id === block.id)) return prev;
      return [...prev, block];
    });
    seenBlockIds.current.add(block.id);
    setActiveBlockId(block.id);
  }, []);

  const openCodeBlock = useCallback((repoName: string, block: CodeBlock) => {
    addCodeBlock(block);
    setState(prev => {
      if (prev.isOpen && prev.repoName === repoName) {
        return { ...prev, targetTab: 'blocks' as const, targetQualifiedName: undefined };
      }
      return { isOpen: true, repoName, targetTab: 'blocks' as const };
    });
  }, [addCodeBlock]);

  const activateBlock = useCallback((file: string, startLine: number, endLine: number) => {
    setCodeBlocks(prev => {
      const block = prev.find(b => b.file === file && b.startLine === startLine && b.endLine === endLine);
      if (block) {
        setActiveBlockId(block.id);
      }
      return prev;
    });
  }, []);

  const closePanel = useCallback(() => {
    setState(prev => ({ ...prev, isOpen: false, targetQualifiedName: undefined, targetTab: undefined }));
  }, []);

  const clearCodeBlocks = useCallback(() => {
    setCodeBlocks([]);
    setActiveBlockId(null);
    seenBlockIds.current.clear();
  }, []);

  return (
    <FloatingPanelContext.Provider value={{
      state, codeBlocks, activeBlockId,
      openRepoViewer, openCodeBlock, addCodeBlock, activateBlock,
      closePanel, clearCodeBlocks,
    }}>
      {children}
    </FloatingPanelContext.Provider>
  );
}

export function useRepoViewer() {
  const ctx = useContext(FloatingPanelContext);
  if (!ctx) throw new Error('useRepoViewer must be used within RepoViewerProvider');
  return ctx;
}
