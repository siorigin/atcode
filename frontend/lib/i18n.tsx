'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';

// Supported languages
export type Language = 'en' | 'zh';

export const LANGUAGES: { code: Language; name: string; nativeName: string }[] = [
  { code: 'en', name: 'English', nativeName: 'English' },
  { code: 'zh', name: 'Chinese', nativeName: '中文' },
];

// Translation dictionary type - supports nested keys
type TranslationValue = string | { [key: string]: TranslationValue };
type TranslationDict = { [key: string]: TranslationValue };

// Context type
interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  isLoading: boolean;
}

const I18nContext = createContext<I18nContextType | undefined>(undefined);

// Storage key for language preference
const LANGUAGE_STORAGE_KEY = 'atcode-language';

// Get nested value from object using dot notation
function getNestedValue(obj: TranslationDict, path: string): string | undefined {
  const keys = path.split('.');
  let current: TranslationValue = obj;

  for (const key of keys) {
    if (current === undefined || current === null || typeof current === 'string') {
      return undefined;
    }
    current = (current as TranslationDict)[key];
  }

  return typeof current === 'string' ? current : undefined;
}

// Replace parameters in translation string
function interpolate(str: string, params?: Record<string, string | number>): string {
  if (!params) return str;

  return str.replace(/\{(\w+)\}/g, (match, key) => {
    return params[key] !== undefined ? String(params[key]) : match;
  });
}

// Cache for loaded translations
const translationCache: Record<Language, TranslationDict | null> = {
  en: null,
  zh: null,
};

// Load translations for a language
async function loadTranslations(lang: Language): Promise<TranslationDict> {
  if (translationCache[lang]) {
    return translationCache[lang]!;
  }

  try {
    const response = await fetch(`/locales/${lang}.json`);
    if (!response.ok) {
      throw new Error(`Failed to load ${lang} translations`);
    }
    const translations = await response.json();
    translationCache[lang] = translations;
    return translations;
  } catch (error) {
    console.error(`Failed to load translations for ${lang}:`, error);
    // Return empty object on error
    return {};
  }
}

// Detect browser language
function detectBrowserLanguage(): Language {
  if (typeof window === 'undefined') return 'en';

  const browserLang = navigator.language.toLowerCase();
  if (browserLang.startsWith('zh')) return 'zh';
  return 'en';
}

// Get saved language preference
function getSavedLanguage(): Language | null {
  if (typeof window === 'undefined') return null;

  const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (saved === 'en' || saved === 'zh') return saved;
  return null;
}

// Save language preference
function saveLanguage(lang: Language): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
}

// Provider component
interface I18nProviderProps {
  children: ReactNode;
  defaultLanguage?: Language;
}

export function I18nProvider({ children, defaultLanguage }: I18nProviderProps) {
  const [language, setLanguageState] = useState<Language>(defaultLanguage || 'en');
  const [translations, setTranslations] = useState<TranslationDict>({});
  const [isLoading, setIsLoading] = useState(true);

  // Initialize language on mount
  useEffect(() => {
    const savedLang = getSavedLanguage();
    const initialLang = savedLang || defaultLanguage || detectBrowserLanguage();
    setLanguageState(initialLang);
  }, [defaultLanguage]);

  // Load translations when language changes
  useEffect(() => {
    setIsLoading(true);
    loadTranslations(language)
      .then(setTranslations)
      .finally(() => setIsLoading(false));
  }, [language]);

  // Set language and save preference
  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
    saveLanguage(lang);
  }, []);

  // Translation function
  const t = useCallback((key: string, params?: Record<string, string | number>): string => {
    const value = getNestedValue(translations, key);
    if (value === undefined) {
      // Return key as fallback (useful for development)
      console.warn(`Translation missing for key: ${key}`);
      return key;
    }
    return interpolate(value, params);
  }, [translations]);

  const value: I18nContextType = {
    language,
    setLanguage,
    t,
    isLoading,
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

// Hook to use translations
export function useTranslation() {
  const context = useContext(I18nContext);
  if (context === undefined) {
    throw new Error('useTranslation must be used within an I18nProvider');
  }
  return context;
}

// Hook for just the translation function (convenience)
export function useT() {
  const { t } = useTranslation();
  return t;
}
