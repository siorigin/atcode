'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { Component, ErrorInfo, ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional fallback UI. If not provided, children are re-rendered after reset. */
  fallback?: ReactNode;
  /** Key that, when changed, resets the error state (e.g., theme value). */
  resetKey?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Catches render errors in child components and prevents full-page crashes.
 *
 * Usage:
 *   <ErrorBoundary resetKey={theme}>
 *     <ComponentThatMightCrash />
 *   </ErrorBoundary>
 *
 * When `resetKey` changes (e.g., on theme switch), the error state is
 * automatically cleared, giving the subtree a fresh chance to render.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[ErrorBoundary] Caught render error:', error, errorInfo);
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    // Auto-reset when resetKey changes (e.g., theme switch)
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div style={{
          padding: '16px 20px',
          margin: 8,
          borderRadius: 8,
          background: 'rgba(255, 80, 80, 0.08)',
          border: '1px solid rgba(255, 80, 80, 0.2)',
          color: '#ccc',
          fontSize: 13,
          fontFamily: 'monospace',
        }}>
          <div style={{ marginBottom: 6, fontWeight: 600, color: '#ff6b6b' }}>
            Component render error
          </div>
          <div style={{ opacity: 0.7, fontSize: 12, wordBreak: 'break-word' }}>
            {this.state.error?.message || 'Unknown error'}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
