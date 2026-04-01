'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useRef, useEffect } from 'react';
import { useTranslation, LANGUAGES, Language } from '@/lib/i18n';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

interface LanguageSwitcherProps {
  variant?: 'dropdown' | 'buttons';
  className?: string;
}

// Language flag emojis
const LANGUAGE_FLAGS: Record<string, string> = {
  en: '🇺🇸',
  zh: '🇨🇳',
  ja: '🇯🇵',
  ko: '🇰🇷',
  es: '🇪🇸',
  fr: '🇫🇷',
  de: '🇩🇪',
  ru: '🇷🇺',
  pt: '🇧🇷',
  it: '🇮🇹',
};

export function LanguageSwitcher({ variant = 'dropdown', className = '' }: LanguageSwitcherProps) {
  const { language, setLanguage, isLoading } = useTranslation();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const currentLang = LANGUAGES.find(l => l.code === language);
  const currentFlag = LANGUAGE_FLAGS[language] || '🌐';

  if (variant === 'buttons') {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        padding: '4px',
        backgroundColor: colors.bgTertiary,
        borderRadius: '10px',
      }}>
        {LANGUAGES.map((lang) => (
          <button
            key={lang.code}
            onClick={() => setLanguage(lang.code)}
            disabled={isLoading}
            style={{
              padding: '6px 12px',
              fontSize: '13px',
              fontWeight: language === lang.code ? '600' : '400',
              borderRadius: '8px',
              border: 'none',
              backgroundColor: language === lang.code ? colors.accent : 'transparent',
              color: language === lang.code ? '#ffffff' : colors.textSecondary,
              cursor: isLoading ? 'not-allowed' : 'pointer',
              opacity: isLoading ? 0.5 : 1,
              transition: 'all 0.2s ease',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            title={lang.name}
          >
            <span style={{ fontSize: '14px' }}>{LANGUAGE_FLAGS[lang.code] || '🌐'}</span>
            <span>{lang.nativeName}</span>
          </button>
        ))}
      </div>
    );
  }

  // Dropdown variant (default)
  return (
    <div ref={dropdownRef} style={{ position: 'relative' }} className={className}>
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={isLoading}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 14px',
          borderRadius: '10px',
          backgroundColor: colors.card,
          border: `1px solid ${colors.border}`,
          color: colors.text,
          cursor: isLoading ? 'not-allowed' : 'pointer',
          opacity: isLoading ? 0.5 : 1,
          transition: 'all 0.2s ease',
          fontSize: '14px',
          fontWeight: '500',
        }}
        onMouseEnter={(e) => {
          if (!isLoading) {
            e.currentTarget.style.backgroundColor = colors.bgHover;
            e.currentTarget.style.borderColor = colors.accent;
          }
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = colors.card;
          e.currentTarget.style.borderColor = colors.border;
        }}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
      >
        {/* Globe Icon */}
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <line x1="2" y1="12" x2="22" y2="12" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>

        {/* Current Language */}
        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ fontSize: '15px' }}>{currentFlag}</span>
          <span>{currentLang?.nativeName || language}</span>
        </span>

        {/* Chevron */}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            transition: 'transform 0.2s ease',
            transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)',
          }}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            marginTop: '8px',
            minWidth: '180px',
            padding: '6px',
            borderRadius: '12px',
            backgroundColor: colors.card,
            border: `1px solid ${colors.border}`,
            boxShadow: '0 10px 40px rgba(0, 0, 0, 0.15)',
            zIndex: 100,
            animation: 'fadeInDown 0.15s ease-out',
          }}
          role="listbox"
        >
          {LANGUAGES.map((lang) => {
            const isSelected = language === lang.code;
            return (
              <button
                key={lang.code}
                onClick={() => {
                  setLanguage(lang.code);
                  setIsOpen(false);
                }}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 12px',
                  borderRadius: '8px',
                  border: 'none',
                  backgroundColor: isSelected ? colors.accentBg : 'transparent',
                  color: isSelected ? colors.accent : colors.text,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                  fontSize: '14px',
                  fontWeight: isSelected ? '600' : '400',
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) {
                    e.currentTarget.style.backgroundColor = colors.bgTertiary;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                  }
                }}
                role="option"
                aria-selected={isSelected}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '18px' }}>{LANGUAGE_FLAGS[lang.code] || '🌐'}</span>
                  <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
                    <span>{lang.nativeName}</span>
                    <span style={{
                      fontSize: '11px',
                      color: colors.textMuted,
                      fontWeight: '400',
                    }}>
                      {lang.name}
                    </span>
                  </span>
                </span>

                {/* Checkmark for selected */}
                {isSelected && (
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* CSS Animation */}
      <style jsx global>{`
        @keyframes fadeInDown {
          from {
            opacity: 0;
            transform: translateY(-8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  );
}

export default LanguageSwitcher;
