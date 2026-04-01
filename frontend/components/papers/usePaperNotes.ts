// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useRef, useCallback } from 'react';

const STORAGE_PREFIX = 'paper-notes-';

export function usePaperNotes(paperId: string) {
  const [notes, setNotesState] = useState('');
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load from localStorage on mount / paperId change
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_PREFIX + paperId);
      setNotesState(stored || '');
    } catch {
      setNotesState('');
    }
  }, [paperId]);

  const setNotes = useCallback(
    (value: string) => {
      setNotesState(value);
      setSaving(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try {
          localStorage.setItem(STORAGE_PREFIX + paperId, value);
        } catch {}
        setSaving(false);
      }, 500);
    },
    [paperId],
  );

  const clearNotes = useCallback(() => {
    setNotesState('');
    try {
      localStorage.removeItem(STORAGE_PREFIX + paperId);
    } catch {}
  }, [paperId]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return { notes, setNotes, saving, clearNotes };
}
