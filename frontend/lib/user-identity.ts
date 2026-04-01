// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * User Identity Management
 *
 * Generates and manages anonymous user IDs for localStorage isolation.
 * This provides client-side user isolation without requiring authentication.
 */

const USER_ID_KEY = 'atcode-user-id';
const USER_ID_PREFIX = 'user';

/**
 * Generates a random user ID
 */
function generateUserId(): string {
  const timestamp = Date.now().toString(36);
  const randomPart = Math.random().toString(36).substring(2, 10);
  return `${USER_ID_PREFIX}-${timestamp}-${randomPart}`;
}

/**
 * Gets or creates the current user's ID
 * This ID persists across browser sessions
 */
export function getUserId(): string {
  if (typeof window === 'undefined') {
    return 'server-side';
  }

  let userId = localStorage.getItem(USER_ID_KEY);

  if (!userId) {
    userId = generateUserId();
    localStorage.setItem(USER_ID_KEY, userId);
    console.log('🆔 Generated new user ID:', userId);
  }

  return userId;
}

/**
 * Gets the storage key for chat data, scoped to the current user
 */
export function getChatStorageKey(): string {
  const userId = getUserId();
  return `atcode-chat-${userId}`;
}

/**
 * Gets the storage key for theme preference, scoped to the current user
 */
export function getThemeStorageKey(): string {
  const userId = getUserId();
  return `atcode-theme-${userId}`;
}

/**
 * Clears all data for the current user (for logout/reset functionality)
 */
export function clearUserData(): void {
  if (typeof window === 'undefined') return;

  const userId = getUserId();
  const keysToRemove: string[] = [];

  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.includes(userId)) {
      keysToRemove.push(key);
    }
  }

  keysToRemove.forEach(key => localStorage.removeItem(key));
  localStorage.removeItem(USER_ID_KEY);

  console.log('🧹 Cleared user data for:', userId);
}

/**
 * Exports user ID for backend API calls
 * This allows the backend to correlate frontend userId with its own userId
 */
export function getUserIdForApi(): string {
  return getUserId();
}
