'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { FloatingChatWidget } from '@/components/FloatingChatWidget';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [isOpen, setIsOpen] = useState(true);
  const [mounted, setMounted] = useState(false);

  // Get repo name from URL params
  const repoName = Array.isArray(params.repo) ? params.repo[0] : params.repo;

  // Get optional sessionId from catch-all route segment
  const sessionIdParam = params.sessionId;
  const sessionId = Array.isArray(sessionIdParam) ? sessionIdParam[0] : sessionIdParam;

  // Derive display name from repo key (strip _claude suffix if present)
  const displayName = repoName?.replace(/_claude$/, '') || 'Chat';

  // Ensure component is mounted before rendering (hydration safety)
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || !repoName) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', background: colors.bg }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 32, height: 32, border: `2px solid ${colors.accent}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 12px' }} />
          <p style={{ color: colors.textMuted, fontSize: 13 }}>Loading chat...</p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: colors.bg, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Header bar with back navigation */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 16px',
        borderBottom: `1px solid ${colors.border}`,
        background: colors.card,
        flexShrink: 0,
      }}>
        <button
          onClick={() => router.push('/repos')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'none',
            border: 'none',
            color: colors.accent,
            cursor: 'pointer',
            fontSize: 13,
            fontFamily: "'Inter', sans-serif",
            padding: '4px 8px',
            borderRadius: 6,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = colors.bg; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'none'; }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back
        </button>
        <div style={{ width: 1, height: 20, background: colors.border }} />
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.text, fontFamily: "'Inter', sans-serif" }}>
          {displayName}
        </span>
        {sessionId && (
          <span style={{ fontSize: 11, color: colors.textDimmed, fontFamily: "'JetBrains Mono', monospace" }}>
            {sessionId.slice(0, 12)}...
          </span>
        )}
      </div>

      {/* Chat widget fills remaining space */}
      <div style={{ flex: 1, position: 'relative', minHeight: 0 }}>
        <FloatingChatWidget
          repoName={repoName}
          isOpen={isOpen}
          onToggle={() => setIsOpen(!isOpen)}
          initialSessionId={sessionId}
          embedded
        />
      </div>
    </div>
  );
}
