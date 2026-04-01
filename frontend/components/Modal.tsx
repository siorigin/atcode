'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { ReactNode, useEffect } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  maxWidth?: string;
  zIndex?: number;
}

export function Modal({ isOpen, onClose, title, children, maxWidth = '500px', zIndex = 200 }: ModalProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  // Handle escape key to close modal
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('keydown', handleEscape);
    // Prevent body scroll when modal is open
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: theme === 'dark' ? 'rgba(0, 0, 0, 0.7)' : 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: zIndex,
        backdropFilter: 'blur(4px)',
        animation: 'fadeIn 150ms ease-out',
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: colors.card,
          borderRadius: '20px', // --radius-xl
          padding: '24px', // --space-6
          maxWidth: maxWidth,
          width: '90%',
          maxHeight: '85vh',
          overflowY: 'auto',
          boxShadow: `0 24px 48px ${colors.shadowColor}`,
          border: `1px solid ${colors.borderLight}`,
          animation: 'scaleIn 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '24px',
          paddingBottom: '16px',
          borderBottom: `1px solid ${colors.borderLight}`,
        }}>
          <h2 style={{
            fontSize: '20px', // --text-lg
            fontWeight: '600',
            color: colors.text,
            letterSpacing: '-0.01em',
            margin: 0,
          }}>
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close modal"
            style={{
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: colors.bgHover,
              border: 'none',
              borderRadius: '8px',
              fontSize: '18px',
              cursor: 'pointer',
              color: colors.textMuted,
              transition: 'all 150ms ease-out',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.errorBg;
              e.currentTarget.style.color = colors.error;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = colors.bgHover;
              e.currentTarget.style.color = colors.textMuted;
            }}
          >
            ×
          </button>
        </div>
        {/* Modal Content */}
        <div style={{ color: colors.textSecondary }}>
          {children}
        </div>
      </div>
      <style jsx>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes scaleIn {
          from {
            opacity: 0;
            transform: scale(0.96);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
      `}</style>
    </div>
  );
}

