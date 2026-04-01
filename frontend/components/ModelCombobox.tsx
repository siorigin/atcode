// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * ModelCombobox - Editable model selector with preset suggestions
 *
 * A shared combobox component used across Chat, Research, and Overview.
 * Allows users to:
 * - Select from preset models (grouped by tier)
 * - Type custom model names directly
 * - View and select from custom model history
 */

import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { Theme } from '../lib/theme-context';
import { getThemeColors } from '../lib/theme-colors';
import { MODEL_TIERS, ModelPreset, isPresetModel } from '../lib/model-config';
import { useModelHistory } from '../lib/hooks/useModelHistory';

interface ModelComboboxProps {
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  theme: Theme;
  showTiers?: boolean;
  tiers?: Record<string, readonly ModelPreset[]>;
  presets?: readonly ModelPreset[];
  placeholder?: string;
  style?: React.CSSProperties;
}

interface OptionItem {
  model: string;
  label: string;
  group?: string;
  isCustom?: boolean;
}

export function ModelCombobox({
  value,
  onChange,
  disabled = false,
  theme,
  showTiers = true,
  tiers,
  presets,
  placeholder = 'Select or type model...',
  style,
}: ModelComboboxProps) {
  const colors = getThemeColors(theme);
  const { customModels, addCustomModel } = useModelHistory();

  const [inputValue, setInputValue] = useState(value);
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [isFocused, setIsFocused] = useState(false);

  // Track if selection is in progress to prevent blur interference
  const isSelectingRef = useRef(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Build flat list of all options for filtering
  const allOptions = useMemo(() => {
    const options: OptionItem[] = [];

    // Add custom models from history first
    customModels.forEach(model => {
      options.push({
        model,
        label: model,
        group: 'Recent',
        isCustom: true,
      });
    });

    if (presets) {
      // Use provided presets (flat list)
      presets.forEach(preset => {
        options.push({
          model: preset.model,
          label: preset.label,
        });
      });
    } else if (showTiers) {
      // Use provided tiers or fallback to hardcoded MODEL_TIERS
      const tierSource = tiers ?? MODEL_TIERS;
      for (const [key, models] of Object.entries(tierSource)) {
        const groupName = key.charAt(0).toUpperCase() + key.slice(1);
        models.forEach(preset => {
          options.push({ ...preset, group: groupName });
        });
      }
    }

    return options;
  }, [customModels, presets, showTiers, tiers]);

  // Filter options based on input
  // Only filter when user is actively typing (inputValue differs from selected value)
  const filteredOptions = useMemo(() => {
    // If input matches current value, show all options (user just opened dropdown)
    if (!inputValue.trim() || inputValue === value) return allOptions;

    // User is typing/searching, filter options
    const query = inputValue.toLowerCase();
    return allOptions.filter(
      opt =>
        opt.label.toLowerCase().includes(query) ||
        opt.model.toLowerCase().includes(query)
    );
  }, [allOptions, inputValue, value]);

  // Group filtered options for display
  const groupedOptions = useMemo(() => {
    const groups: Record<string, OptionItem[]> = {};
    const ungrouped: OptionItem[] = [];

    filteredOptions.forEach(opt => {
      if (opt.group) {
        if (!groups[opt.group]) groups[opt.group] = [];
        groups[opt.group].push(opt);
      } else {
        ungrouped.push(opt);
      }
    });

    return { groups, ungrouped };
  }, [filteredOptions]);

  // Sync input value when external value changes
  useEffect(() => {
    if (!isFocused) {
      setInputValue(value);
    }
  }, [value, isFocused]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setInputValue(value);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [value]);

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlightedIndex >= 0 && dropdownRef.current) {
      const items = dropdownRef.current.querySelectorAll('[data-option-index]');
      const item = items[highlightedIndex] as HTMLElement;
      if (item) {
        item.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [highlightedIndex]);

  const selectOption = useCallback(
    (option: OptionItem | string) => {
      const model = typeof option === 'string' ? option : option.model;

      // Save custom model to history
      if (!isPresetModel(model) && model.trim()) {
        addCustomModel(model);
      }

      onChange(model);
      setInputValue(model);
      setIsOpen(false);
      setHighlightedIndex(-1);
      isSelectingRef.current = false;
    },
    [onChange, addCustomModel]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value);
    setIsOpen(true);
    setHighlightedIndex(-1);
  };

  const handleInputFocus = () => {
    setIsFocused(true);
    setIsOpen(true);
    inputRef.current?.select();
  };

  const handleInputBlur = () => {
    setIsFocused(false);

    // If selection is in progress, don't interfere
    if (isSelectingRef.current) {
      return;
    }

    // Auto-apply the input value when losing focus
    if (inputValue.trim() && inputValue !== value) {
      onChange(inputValue.trim());
      if (!isPresetModel(inputValue.trim())) {
        addCustomModel(inputValue.trim());
      }
    }
  };

  // Handle option selection via mousedown (before blur)
  const handleOptionMouseDown = (e: React.MouseEvent, option: OptionItem) => {
    e.preventDefault(); // Prevent blur from firing
    isSelectingRef.current = true;
    selectOption(option);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const totalOptions = filteredOptions.length;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        }
        setHighlightedIndex(prev => (prev < totalOptions - 1 ? prev + 1 : 0));
        break;

      case 'ArrowUp':
        e.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        }
        setHighlightedIndex(prev => (prev > 0 ? prev - 1 : totalOptions - 1));
        break;

      case 'Enter':
        e.preventDefault();
        if (highlightedIndex >= 0 && filteredOptions[highlightedIndex]) {
          selectOption(filteredOptions[highlightedIndex]);
        } else if (inputValue.trim()) {
          selectOption(inputValue.trim());
        }
        break;

      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        setInputValue(value);
        inputRef.current?.blur();
        break;

      case 'Tab':
        if (inputValue.trim() && inputValue !== value) {
          selectOption(inputValue.trim());
        }
        setIsOpen(false);
        break;
    }
  };

  const handleToggleDropdown = (e: React.MouseEvent) => {
    e.preventDefault(); // Prevent blur
    if (!disabled) {
      setIsOpen(!isOpen);
      if (!isOpen) {
        inputRef.current?.focus();
      }
    }
  };

  // Check if current input is a custom model (not in presets)
  const isCustomInput = inputValue.trim() && !filteredOptions.some(
    opt => opt.label.toLowerCase() === inputValue.toLowerCase() ||
           opt.model.toLowerCase() === inputValue.toLowerCase()
  );

  const borderColor = isFocused ? colors.accentBorder : colors.inputBorder;

  // Render a single option item
  const renderOption = (option: OptionItem, globalIndex: number) => {
    const isHighlighted = globalIndex === highlightedIndex;
    const isSelected = option.model === value;
    const showLabel = option.label !== option.model && !option.isCustom;

    return (
      <div
        key={option.model}
        data-option-index={globalIndex}
        onMouseDown={(e) => handleOptionMouseDown(e, option)}
        onMouseEnter={() => setHighlightedIndex(globalIndex)}
        title={`${option.model}${showLabel ? ` (${option.label})` : ''}`}
        style={{
          padding: '8px 12px',
          fontSize: '13px',
          cursor: 'pointer',
          backgroundColor: isHighlighted
            ? colors.bgHover
            : isSelected
            ? colors.accentBg
            : 'transparent',
          color: isSelected ? colors.accent : colors.text,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
          transition: 'background-color 100ms ease-out',
        }}
      >
        <span style={{
          flex: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontFamily: 'monospace',
          fontSize: '12px',
        }}>
          {option.model}
        </span>
        {showLabel && (
          <span style={{
            fontSize: '11px',
            color: colors.textMuted,
            flexShrink: 0,
          }}>
            {option.label}
          </span>
        )}
        {option.isCustom && (
          <span style={{
            fontSize: '11px',
            color: colors.textMuted,
            padding: '2px 6px',
            backgroundColor: colors.bgTertiary,
            borderRadius: '4px',
            flexShrink: 0,
          }}>
            custom
          </span>
        )}
      </div>
    );
  };

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        display: 'inline-block',
        ...style,
      }}
    >
      {/* Input with dropdown toggle */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          borderRadius: '6px',
          border: `1px solid ${borderColor}`,
          backgroundColor: disabled ? colors.bgTertiary : colors.inputBg,
          transition: 'border-color 150ms ease-out, box-shadow 150ms ease-out',
          boxShadow: isFocused ? `0 0 0 2px ${colors.accentBg}` : 'none',
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onFocus={handleInputFocus}
          onBlur={handleInputBlur}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          title={value || 'Select AI Model'}
          style={{
            flex: 1,
            padding: '6px 0 6px 10px',
            border: 'none',
            backgroundColor: 'transparent',
            color: colors.inputText,
            fontSize: '13px',
            fontWeight: 500,
            fontFamily: 'monospace',
            outline: 'none',
            cursor: disabled ? 'not-allowed' : 'text',
            minWidth: 0,
          }}
        />
        {/* Dropdown toggle button */}
        <button
          type="button"
          onMouseDown={handleToggleDropdown}
          disabled={disabled}
          tabIndex={-1}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '6px 8px',
            border: 'none',
            backgroundColor: 'transparent',
            cursor: disabled ? 'not-allowed' : 'pointer',
            color: colors.textSecondary,
          }}
          aria-label="Toggle dropdown"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: isOpen ? 'rotate(0deg)' : 'rotate(180deg)',
              transition: 'transform 150ms ease-out',
            }}
          >
            <path d="M6 15l6-6 6 6" />
          </svg>
        </button>
      </div>

      {/* Dropdown - opens upward to avoid being clipped */}
      {isOpen && !disabled && (
        <div
          ref={dropdownRef}
          style={{
            position: 'absolute',
            bottom: '100%',
            left: 0,
            right: 0,
            marginBottom: '4px',
            maxHeight: '300px',
            overflowY: 'auto',
            backgroundColor: colors.bgSecondary,
            border: `1px solid ${colors.border}`,
            borderRadius: '6px',
            boxShadow: `0 -4px 12px ${colors.shadowColor}`,
            zIndex: 1000,
          }}
        >
          {/* Custom input hint */}
          {isCustomInput && (
            <div
              style={{
                padding: '8px 12px',
                fontSize: '12px',
                color: colors.textSecondary,
                borderBottom: `1px solid ${colors.borderLight}`,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <span style={{ color: colors.accent }}>↵</span>
              <span>Press Enter to use:</span>
              <strong style={{ color: colors.text, fontFamily: 'monospace' }}>{inputValue}</strong>
            </div>
          )}

          {/* Grouped options */}
          {Object.entries(groupedOptions.groups).map(([groupName, options]) => (
            options.length > 0 && (
              <div key={groupName}>
                <div
                  style={{
                    padding: '6px 12px',
                    fontSize: '11px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    color: colors.textMuted,
                    backgroundColor: colors.bgTertiary,
                    borderBottom: `1px solid ${colors.borderLight}`,
                  }}
                >
                  {groupName}
                </div>
                {options.map((option) => {
                  const globalIndex = filteredOptions.indexOf(option);
                  return renderOption(option, globalIndex);
                })}
              </div>
            )
          ))}

          {/* Ungrouped options */}
          {groupedOptions.ungrouped.length > 0 && (
            <>
              {Object.keys(groupedOptions.groups).length > 0 && (
                <div
                  style={{
                    padding: '6px 12px',
                    fontSize: '11px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    color: colors.textMuted,
                    backgroundColor: colors.bgTertiary,
                    borderBottom: `1px solid ${colors.borderLight}`,
                  }}
                >
                  Presets
                </div>
              )}
              {groupedOptions.ungrouped.map((option) => {
                const globalIndex = filteredOptions.indexOf(option);
                return renderOption(option, globalIndex);
              })}
            </>
          )}

          {/* Empty state */}
          {filteredOptions.length === 0 && !isCustomInput && (
            <div
              style={{
                padding: '12px',
                fontSize: '12px',
                color: colors.textMuted,
                textAlign: 'center',
              }}
            >
              No matching models
            </div>
          )}
        </div>
      )}
    </div>
  );
}
