'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { createContext, useContext, useState, ReactNode, useEffect, useRef } from 'react';

interface Toast {
  id: string;
  type: 'success' | 'error' | 'info';
  message: string;
}

interface ToastContextType {
  showToast: (type: Toast['type'], message: string) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);
const TOAST_DEDUP_WINDOW_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const recentToastTimestampsRef = useRef<Map<string, number>>(new Map());

  const showToast = (type: Toast['type'], message: string) => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage) {
      return;
    }

    const now = Date.now();
    const toastKey = `${type}:${trimmedMessage}`;
    const recentToastTimestamps = recentToastTimestampsRef.current;

    for (const [key, timestamp] of recentToastTimestamps.entries()) {
      if (now - timestamp > TOAST_DEDUP_WINDOW_MS) {
        recentToastTimestamps.delete(key);
      }
    }

    const lastShownAt = recentToastTimestamps.get(toastKey);
    if (lastShownAt && now - lastShownAt < TOAST_DEDUP_WINDOW_MS) {
      return;
    }

    recentToastTimestamps.set(toastKey, now);
    const id = `toast-${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev, { id, type, message: trimmedMessage }]);

    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 3000);
  };

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div
        style={{
          position: 'fixed',
          top: '24px',
          right: '24px',
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          pointerEvents: 'none'
        }}
      >
        {toasts.map(toast => (
          <ToastItem key={toast.id} toast={toast} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast }: { toast: Toast }) {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    // Trigger animation
    setTimeout(() => setIsVisible(true), 10);
  }, []);

  const getStyles = () => {
    const baseStyles = {
      padding: '12px 20px',
      borderRadius: '12px',
      boxShadow: '0 8px 24px rgba(0, 0, 0, 0.3)',
      color: '#ffffff',
      fontWeight: '500' as const,
      fontSize: '14px',
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      minWidth: '300px',
      pointerEvents: 'auto' as const,
      transform: isVisible ? 'translateX(0)' : 'translateX(400px)',
      opacity: isVisible ? 1 : 0,
      transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
    };

    const typeStyles = {
      success: {
        background: 'linear-gradient(135deg, #2da44e 0%, #26a641 100%)',
      },
      error: {
        background: 'linear-gradient(135deg, #cf222e 0%, #d1242f 100%)',
      },
      info: {
        background: 'linear-gradient(135deg, #0969da 0%, #0860ca 100%)',
      },
    };

    return { ...baseStyles, ...typeStyles[toast.type] };
  };

  const getIcon = () => {
    switch (toast.type) {
      case 'success':
        return '✓';
      case 'error':
        return '✕';
      case 'info':
        return 'ℹ';
    }
  };

  return (
    <div style={getStyles()}>
      <span
        style={{
          fontSize: '18px',
          fontWeight: 'bold',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '24px',
          height: '24px',
          borderRadius: '50%',
          background: 'rgba(255, 255, 255, 0.2)',
        }}
      >
        {getIcon()}
      </span>
      <span>{toast.message}</span>
    </div>
  );
}

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context;
};
