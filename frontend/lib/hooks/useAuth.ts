// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * useAuth Hook
 *
 * Frontend hook for managing user authentication state.
 * Syncs with backend JWT authentication and localStorage userId.
 */

import { useState, useEffect, useCallback } from 'react';
import { getUserId } from '../user-identity';

interface User {
  userId: string;
  isAnonymous: boolean;
  createdAt?: number;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  error: string | null;
  isAuthenticated: boolean;
}

interface UseAuthReturn extends AuthState {
  refreshAuth: () => Promise<void>;
  logout: () => Promise<void>;
  syncWithBackend: () => Promise<void>;
}

/**
 * Hook for managing user authentication
 */
export function useAuth(): UseAuthReturn {
  const [state, setState] = useState<AuthState>({
    user: null,
    isLoading: true,
    error: null,
    isAuthenticated: false,
  });

  const fetchUser = useCallback(async () => {
    try {
      setState(s => ({ ...s, isLoading: true, error: null }));

      const response = await fetch('/api/auth/me');
      const data = await response.json();

      if (data.success) {
        setState({
          user: data.user,
          isLoading: false,
          error: null,
          isAuthenticated: true,
        });
      } else {
        setState({
          user: null,
          isLoading: false,
          error: data.error,
          isAuthenticated: false,
        });
      }
    } catch (error) {
      console.error('Auth fetch error:', error);
      setState({
        user: null,
        isLoading: false,
        error: 'Failed to fetch user',
        isAuthenticated: false,
      });
    }
  }, []);

  const syncWithBackend = useCallback(async () => {
    try {
      // Get the frontend userId from localStorage
      const frontendUserId = getUserId();

      // Sync with backend
      const response = await fetch('/api/auth/me', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ userId: frontendUserId }),
      });

      const data = await response.json();

      if (data.success) {
        setState({
          user: data.user,
          isLoading: false,
          error: null,
          isAuthenticated: true,
        });
        console.log('Auth synced with backend:', data.user.userId);
      }
    } catch (error) {
      console.error('Auth sync error:', error);
    }
  }, []);

  const refreshAuth = useCallback(async () => {
    await fetchUser();
  }, [fetchUser]);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/me', { method: 'DELETE' });
      setState({
        user: null,
        isLoading: false,
        error: null,
        isAuthenticated: false,
      });
    } catch (error) {
      console.error('Logout error:', error);
    }
  }, []);

  // Initial fetch on mount
  useEffect(() => {
    syncWithBackend();
  }, [syncWithBackend]);

  return {
    ...state,
    refreshAuth,
    logout,
    syncWithBackend,
  };
}

/**
 * Hook for getting just the user ID (lightweight)
 */
export function useUserId(): string {
  const [userId, setUserId] = useState<string>('');

  useEffect(() => {
    // Get from localStorage first (immediate)
    const localUserId = getUserId();
    setUserId(localUserId);
  }, []);

  return userId;
}
