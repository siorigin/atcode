'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useRef } from 'react';
import { useTheme } from '../../lib/theme-context';
import { getThemeColors } from '../../lib/theme-colors';
import { getPaperStatus } from '../../lib/papers-api';

interface PaperTaskProgressProps {
  taskId: string;
  onComplete: (result: any) => void;
  onDismiss: () => void;
}

export default function PaperTaskProgress({ taskId, onComplete, onDismiss }: PaperTaskProgressProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('pending');
  const [step, setStep] = useState('Starting pipeline...');
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const data = await getPaperStatus(taskId);
        setProgress(data.progress);
        setStatus(data.status);
        setStep(data.status_message || data.step || '');

        if (data.status === 'completed') {
          if (intervalRef.current) clearInterval(intervalRef.current);
          onComplete(data.result);
        } else if (data.status === 'failed') {
          if (intervalRef.current) clearInterval(intervalRef.current);
          setError(data.error || 'Pipeline failed');
        }
      } catch (e) {
        // Ignore polling errors
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 3000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [taskId, onComplete]);

  const isTerminal = status === 'completed' || status === 'failed' || status === 'cancelled';

  return (
    <div
      style={{
        background: error ? colors.errorBg : colors.accentBg,
        border: `1px solid ${error ? colors.errorBorder : colors.accentBorder}`,
        borderRadius: 8,
        padding: 16,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>
          {error ? 'Pipeline Failed' : isTerminal ? 'Pipeline Complete' : 'Paper Reading Pipeline'}
        </div>
        {isTerminal && (
          <button
            onClick={onDismiss}
            style={{
              background: 'none',
              border: 'none',
              color: colors.textMuted,
              cursor: 'pointer',
              fontSize: 16,
            }}
          >
            ×
          </button>
        )}
      </div>

      {/* Progress bar */}
      <div style={{
        height: 6,
        background: colors.border,
        borderRadius: 3,
        overflow: 'hidden',
        marginBottom: 8,
      }}>
        <div
          style={{
            height: '100%',
            width: `${progress}%`,
            background: error ? colors.error : colors.accent,
            borderRadius: 3,
            transition: 'width 0.3s ease',
          }}
        />
      </div>

      <div style={{ fontSize: 13, color: colors.textSecondary }}>
        {error || step || `${progress}% complete`}
      </div>
    </div>
  );
}
