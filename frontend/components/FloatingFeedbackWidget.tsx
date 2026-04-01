'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useCallback } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useToast } from '@/components/Toast';
import { apiFetch } from '@/lib/api-client';

// Feedback item type
interface FeedbackItem {
  id: string;
  title: string;
  description: string;
  category: 'bug' | 'feature' | 'improvement' | 'question' | 'other';
  status: 'open' | 'resolved';
  author: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

// Category config
const CATEGORIES = [
  { value: 'bug', label: 'Bug', icon: '🐛' },
  { value: 'feature', label: 'Feature Request', icon: '✨' },
  { value: 'improvement', label: 'Improvement', icon: '💡' },
  { value: 'question', label: 'Question', icon: '❓' },
  { value: 'other', label: 'Other', icon: '📝' },
] as const;

interface FloatingFeedbackWidgetProps {
  isOpen: boolean;
  onToggle: () => void;
}

export function FloatingFeedbackWidget({ isOpen, onToggle }: FloatingFeedbackWidgetProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { showToast } = useToast();

  // State
  const [activeTab, setActiveTab] = useState<'list' | 'create'>('list');
  const [statusFilter, setStatusFilter] = useState<'all' | 'open' | 'resolved'>('all');
  const [feedbackList, setFeedbackList] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [counts, setCounts] = useState({ open: 0, resolved: 0, total: 0 });

  // Form state
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<typeof CATEGORIES[number]['value']>('other');
  const [author, setAuthor] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Load feedback list
  const loadFeedback = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch(`/api/feedback?status=${statusFilter}`);
      if (response.ok) {
        const data = await response.json();
        setFeedbackList(data.feedback || []);
        setCounts({
          open: data.open_count || 0,
          resolved: data.resolved_count || 0,
          total: data.total || 0,
        });
      }
    } catch (error) {
      console.error('Failed to load feedback:', error);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  // Load feedback when panel opens or filter changes
  useEffect(() => {
    if (isOpen) {
      loadFeedback();
    }
  }, [isOpen, loadFeedback]);

  // Submit new feedback
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !description.trim()) {
      showToast('error', 'Please fill in all required fields');
      return;
    }

    setSubmitting(true);
    try {
      const response = await apiFetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim(),
          category,
          author: author.trim() || null,
        }),
      });

      if (response.ok) {
        showToast('success', 'Feedback submitted successfully');
        setTitle('');
        setDescription('');
        setCategory('other');
        setAuthor('');
        setActiveTab('list');
        loadFeedback();
      } else {
        const error = await response.json();
        showToast('error', error.error || 'Failed to submit feedback');
      }
    } catch (error) {
      showToast('error', 'Failed to submit feedback');
    } finally {
      setSubmitting(false);
    }
  };

  // Toggle feedback status
  const toggleStatus = async (item: FeedbackItem) => {
    const newStatus = item.status === 'open' ? 'resolved' : 'open';
    try {
      const response = await apiFetch(`/api/feedback/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });

      if (response.ok) {
        showToast('success', `Marked as ${newStatus}`);
        loadFeedback();
      } else {
        showToast('error', 'Failed to update status');
      }
    } catch (error) {
      showToast('error', 'Failed to update status');
    }
  };

  // Delete feedback
  const deleteFeedback = async (id: string) => {
    try {
      const response = await apiFetch(`/api/feedback/${id}`, {
        method: 'DELETE',
      });

      if (response.ok) {
        showToast('success', 'Feedback deleted');
        loadFeedback();
      } else {
        showToast('error', 'Failed to delete feedback');
      }
    } catch (error) {
      showToast('error', 'Failed to delete feedback');
    }
  };

  // Format date
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
      if (diffHours === 0) {
        const diffMins = Math.floor(diffMs / (1000 * 60));
        return diffMins <= 1 ? 'just now' : `${diffMins}m ago`;
      }
      return `${diffHours}h ago`;
    } else if (diffDays === 1) {
      return 'yesterday';
    } else if (diffDays < 7) {
      return `${diffDays}d ago`;
    } else {
      return date.toLocaleDateString();
    }
  };

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '24px',
        left: '24px',
        width: '420px',
        maxWidth: 'calc(100vw - 48px)',
        maxHeight: 'calc(100vh - 100px)',
        background: colors.card,
        border: `1px solid ${colors.border}`,
        borderRadius: '16px',
        boxShadow: `0 20px 60px ${colors.shadowColor}`,
        display: 'flex',
        flexDirection: 'column',
        zIndex: 1000,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '16px 20px',
          borderBottom: `1px solid ${colors.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: colors.bgSecondary,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '20px' }}>📋</span>
          <span style={{ fontSize: '16px', fontWeight: '600', color: colors.text }}>
            Feedback
          </span>
          {counts.open > 0 && (
            <span
              style={{
                padding: '2px 8px',
                background: colors.warningBg,
                border: `1px solid ${colors.warningBorder}`,
                borderRadius: '12px',
                fontSize: '11px',
                fontWeight: '600',
                color: colors.warning,
              }}
            >
              {counts.open} open
            </span>
          )}
        </div>
        <button
          onClick={onToggle}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '4px 8px',
            color: colors.textMuted,
            fontSize: '18px',
            lineHeight: 1,
            borderRadius: '4px',
            transition: 'all 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.bgHover;
            e.currentTarget.style.color = colors.text;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.color = colors.textMuted;
          }}
        >
          ×
        </button>
      </div>

      {/* Tabs */}
      <div
        style={{
          display: 'flex',
          borderBottom: `1px solid ${colors.border}`,
          background: colors.bgSecondary,
        }}
      >
        {[
          { key: 'list', label: 'All Feedback', icon: '📝' },
          { key: 'create', label: 'New Feedback', icon: '➕' },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as 'list' | 'create')}
            style={{
              flex: 1,
              padding: '12px 16px',
              background: activeTab === tab.key ? colors.card : 'transparent',
              border: 'none',
              borderBottom: activeTab === tab.key ? `2px solid ${colors.accent}` : '2px solid transparent',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: activeTab === tab.key ? '600' : '400',
              color: activeTab === tab.key ? colors.accent : colors.textMuted,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              if (activeTab !== tab.key) {
                e.currentTarget.style.background = colors.bgHover;
              }
            }}
            onMouseLeave={(e) => {
              if (activeTab !== tab.key) {
                e.currentTarget.style.background = 'transparent';
              }
            }}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {activeTab === 'list' ? (
          <>
            {/* Status Filter */}
            <div
              style={{
                padding: '12px 16px',
                display: 'flex',
                gap: '8px',
                borderBottom: `1px solid ${colors.borderLight}`,
              }}
            >
              {[
                { key: 'all', label: `All (${counts.open + counts.resolved})` },
                { key: 'open', label: `Open (${counts.open})` },
                { key: 'resolved', label: `Resolved (${counts.resolved})` },
              ].map((filter) => (
                <button
                  key={filter.key}
                  onClick={() => setStatusFilter(filter.key as 'all' | 'open' | 'resolved')}
                  style={{
                    padding: '6px 12px',
                    background: statusFilter === filter.key ? colors.accentBg : colors.bgTertiary,
                    border: `1px solid ${statusFilter === filter.key ? colors.accentBorder : colors.borderLight}`,
                    borderRadius: '16px',
                    cursor: 'pointer',
                    fontSize: '12px',
                    fontWeight: statusFilter === filter.key ? '600' : '400',
                    color: statusFilter === filter.key ? colors.accent : colors.textSecondary,
                    transition: 'all 0.15s',
                  }}
                >
                  {filter.label}
                </button>
              ))}
            </div>

            {/* Feedback List */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
              {loading ? (
                <div style={{ padding: '32px', textAlign: 'center', color: colors.textMuted }}>
                  <div
                    style={{
                      width: '24px',
                      height: '24px',
                      border: `2px solid ${colors.border}`,
                      borderTopColor: colors.accent,
                      borderRadius: '50%',
                      animation: 'spin 0.8s linear infinite',
                      margin: '0 auto 8px',
                    }}
                  />
                  Loading...
                </div>
              ) : feedbackList.length === 0 ? (
                <div style={{ padding: '48px 24px', textAlign: 'center', color: colors.textMuted }}>
                  <div style={{ fontSize: '36px', marginBottom: '12px', opacity: 0.5 }}>📭</div>
                  <div style={{ fontSize: '14px', fontWeight: '500' }}>No feedback yet</div>
                  <div style={{ fontSize: '12px', marginTop: '4px', opacity: 0.8 }}>
                    Be the first to share your thoughts!
                  </div>
                </div>
              ) : (
                feedbackList.map((item) => (
                  <div
                    key={item.id}
                    style={{
                      padding: '14px 16px',
                      background: colors.bgSecondary,
                      border: `1px solid ${colors.borderLight}`,
                      borderRadius: '12px',
                      marginBottom: '8px',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = colors.border;
                      e.currentTarget.style.background = colors.bgTertiary;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = colors.borderLight;
                      e.currentTarget.style.background = colors.bgSecondary;
                    }}
                  >
                    {/* Header Row */}
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', marginBottom: '8px' }}>
                      {/* Category Icon */}
                      <span style={{ fontSize: '16px' }}>
                        {CATEGORIES.find((c) => c.value === item.category)?.icon || '📝'}
                      </span>
                      {/* Title & Meta */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            fontSize: '14px',
                            fontWeight: '600',
                            color: colors.text,
                            marginBottom: '4px',
                            wordBreak: 'break-word',
                          }}
                        >
                          {item.title}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                          <span
                            style={{
                              padding: '2px 8px',
                              background: item.status === 'open' ? colors.warningBg : colors.successBg,
                              border: `1px solid ${item.status === 'open' ? colors.warningBorder : colors.successBorder}`,
                              borderRadius: '10px',
                              fontSize: '10px',
                              fontWeight: '600',
                              color: item.status === 'open' ? colors.warning : colors.success,
                              textTransform: 'uppercase',
                            }}
                          >
                            {item.status}
                          </span>
                          <span style={{ fontSize: '11px', color: colors.textMuted }}>
                            {item.author} · {formatDate(item.created_at)}
                          </span>
                        </div>
                      </div>
                    </div>
                    {/* Description */}
                    <div
                      style={{
                        fontSize: '13px',
                        color: colors.textSecondary,
                        lineHeight: '1.5',
                        marginBottom: '10px',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {item.description.length > 200 ? item.description.slice(0, 200) + '...' : item.description}
                    </div>
                    {/* Actions */}
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        onClick={() => toggleStatus(item)}
                        style={{
                          padding: '5px 10px',
                          background: item.status === 'open' ? colors.successBg : colors.warningBg,
                          border: `1px solid ${item.status === 'open' ? colors.successBorder : colors.warningBorder}`,
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '11px',
                          fontWeight: '500',
                          color: item.status === 'open' ? colors.success : colors.warning,
                          transition: 'all 0.15s',
                        }}
                      >
                        {item.status === 'open' ? '✓ Mark Resolved' : '↻ Reopen'}
                      </button>
                      <button
                        onClick={() => deleteFeedback(item.id)}
                        style={{
                          padding: '5px 10px',
                          background: 'transparent',
                          border: `1px solid ${colors.borderLight}`,
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '11px',
                          fontWeight: '500',
                          color: colors.textMuted,
                          transition: 'all 0.15s',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = colors.errorBg;
                          e.currentTarget.style.borderColor = colors.errorBorder;
                          e.currentTarget.style.color = colors.error;
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.borderColor = colors.borderLight;
                          e.currentTarget.style.color = colors.textMuted;
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        ) : (
          /* Create Form */
          <form onSubmit={handleSubmit} style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px' }}>
            {/* Title */}
            <div style={{ marginBottom: '14px' }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: '6px',
                  fontSize: '13px',
                  fontWeight: '500',
                  color: colors.text,
                }}
              >
                Title <span style={{ color: colors.error }}>*</span>
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Brief summary of your feedback"
                maxLength={200}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: colors.inputBg,
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '8px',
                  color: colors.inputText,
                  fontSize: '14px',
                  outline: 'none',
                  transition: 'border-color 0.15s',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = colors.accent;
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = colors.inputBorder;
                }}
              />
            </div>

            {/* Category */}
            <div style={{ marginBottom: '14px' }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: '6px',
                  fontSize: '13px',
                  fontWeight: '500',
                  color: colors.text,
                }}
              >
                Category
              </label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {CATEGORIES.map((cat) => (
                  <button
                    key={cat.value}
                    type="button"
                    onClick={() => setCategory(cat.value)}
                    style={{
                      padding: '6px 12px',
                      background: category === cat.value ? colors.accentBg : colors.bgTertiary,
                      border: `1px solid ${category === cat.value ? colors.accentBorder : colors.borderLight}`,
                      borderRadius: '16px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: category === cat.value ? '600' : '400',
                      color: category === cat.value ? colors.accent : colors.textSecondary,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      transition: 'all 0.15s',
                    }}
                  >
                    <span>{cat.icon}</span>
                    <span>{cat.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Description */}
            <div style={{ marginBottom: '14px', flex: 1, display: 'flex', flexDirection: 'column' }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: '6px',
                  fontSize: '13px',
                  fontWeight: '500',
                  color: colors.text,
                }}
              >
                Description <span style={{ color: colors.error }}>*</span>
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe your feedback in detail..."
                maxLength={2000}
                style={{
                  flex: 1,
                  minHeight: '120px',
                  padding: '10px 12px',
                  background: colors.inputBg,
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '8px',
                  color: colors.inputText,
                  fontSize: '14px',
                  outline: 'none',
                  resize: 'none',
                  fontFamily: 'inherit',
                  transition: 'border-color 0.15s',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = colors.accent;
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = colors.inputBorder;
                }}
              />
              <div style={{ textAlign: 'right', fontSize: '11px', color: colors.textMuted, marginTop: '4px' }}>
                {description.length}/2000
              </div>
            </div>

            {/* Author */}
            <div style={{ marginBottom: '16px' }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: '6px',
                  fontSize: '13px',
                  fontWeight: '500',
                  color: colors.text,
                }}
              >
                Your Name <span style={{ color: colors.textMuted, fontWeight: '400' }}>(optional)</span>
              </label>
              <input
                type="text"
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                placeholder="Anonymous"
                maxLength={50}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: colors.inputBg,
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '8px',
                  color: colors.inputText,
                  fontSize: '14px',
                  outline: 'none',
                  transition: 'border-color 0.15s',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = colors.accent;
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = colors.inputBorder;
                }}
              />
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={submitting || !title.trim() || !description.trim()}
              style={{
                padding: '12px 20px',
                background: submitting || !title.trim() || !description.trim() ? colors.textMuted : colors.buttonPrimaryBg,
                border: 'none',
                borderRadius: '8px',
                cursor: submitting || !title.trim() || !description.trim() ? 'not-allowed' : 'pointer',
                fontSize: '14px',
                fontWeight: '600',
                color: '#ffffff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                if (!submitting && title.trim() && description.trim()) {
                  e.currentTarget.style.background = colors.buttonPrimaryHover;
                }
              }}
              onMouseLeave={(e) => {
                if (!submitting && title.trim() && description.trim()) {
                  e.currentTarget.style.background = colors.buttonPrimaryBg;
                }
              }}
            >
              {submitting && (
                <div
                  style={{
                    width: '14px',
                    height: '14px',
                    border: '2px solid #ffffff',
                    borderTopColor: 'transparent',
                    borderRadius: '50%',
                    animation: 'spin 0.6s linear infinite',
                  }}
                />
              )}
              {submitting ? 'Submitting...' : 'Submit Feedback'}
            </button>
          </form>
        )}
      </div>

      <style jsx>{`
        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </div>
  );
}
