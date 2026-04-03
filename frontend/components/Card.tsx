'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { ReactNode, CSSProperties } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { useTranslation } from '@/lib/i18n';

type GraphOperationStatus = 'queued' | 'generating' | 'cleaning' | null;

interface BaseCardProps {
  onClick?: () => void;
  onDoubleClick?: () => void;
  theme?: 'dark' | 'light' | 'beige';
  className?: string;
  style?: CSSProperties;
  // Selection support
  isSelected?: boolean;
  isSelectable?: boolean;
  onSelect?: (e: React.MouseEvent) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  // Drag-drop support
  draggable?: boolean;
  onDragStart?: (e: React.DragEvent) => void;
  onDragEnd?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
  isDragOver?: boolean;
  // Ref for lasso selection
  cardRef?: (element: HTMLElement | null) => void;
  cardId?: string;
}

interface RepoCardProps extends BaseCardProps {
  type: 'repo';
  name: string;
  path?: string;  // Local project path for disambiguation
  researchCount?: number;
  lastUpdated?: string;
  hasDocs?: boolean;
  isNew?: boolean;
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
  // Knowledge graph status
  hasGraph?: boolean;
  graphNodeCount?: number;
  graphRelationshipCount?: number;
  graphOperationStatus?: GraphOperationStatus;
  // Sync status (persisted in Memgraph)
  syncEnabled?: boolean;
}

interface OperatorCardProps extends BaseCardProps {
  type: 'operator';
  name: string;
  description?: string;
  lastUpdated?: string;
  referencesCount?: number;
  codeBlocksCount?: number;
  hasDoc?: boolean;
  isNew?: boolean;
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
}

interface JobCardProps extends BaseCardProps {
  type: 'job';
  name: string;
  status: 'pending' | 'running' | 'stalled' | 'completed' | 'failed';
  progress: number;
  currentStep?: string;
}

interface AddCardProps extends BaseCardProps {
  type: 'add';
  label: string;
  icon?: string;
}

interface FolderCardProps extends BaseCardProps {
  type: 'folder';
  name: string;
  documentCount?: number;
  lastUpdated?: string;
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
}

type CardProps = RepoCardProps | OperatorCardProps | JobCardProps | AddCardProps | FolderCardProps;

export function Card(props: CardProps) {
  const {
    onClick,
    onDoubleClick,
    theme = 'dark',
    className = '',
    style = {},
    isSelected = false,
    isSelectable = false,
    onSelect,
    onContextMenu,
    draggable = false,
    onDragStart,
    onDragEnd,
    onDragOver,
    onDragLeave,
    onDrop,
    isDragOver = false,
    cardRef,
    cardId,
  } = props;
  const colors = getThemeColors(theme);

  // Using design token values for consistency
  const baseStyles: CSSProperties = {
    borderRadius: '14px', // --radius-lg
    padding: '24px', // --space-6
    cursor: onClick || isSelectable ? 'pointer' : 'default',
    transition: 'all 250ms cubic-bezier(0.16, 1, 0.3, 1)', // --ease-smooth
    minHeight: '160px',
    position: 'relative',
    overflow: 'visible',
    ...style
  };

  const getCardStyles = (): CSSProperties => {
    // Selection styling
    const selectionBorder = isSelected ? `2px solid ${colors.accent}` : undefined;
    const selectionShadow = isSelected ? `0 0 0 3px ${colors.accent}30` : undefined;
    const selectionBg = isSelected ? `${colors.accent}10` : undefined;

    // Drag-over styling
    const dragOverBorder = isDragOver ? `2px dashed ${colors.accent}` : undefined;
    const dragOverBg = isDragOver ? `${colors.accent}10` : undefined;

    if (props.type === 'add') {
      return {
        ...baseStyles,
        background: colors.gradientPrimary,
        border: `2px dashed ${colors.border}`,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: `0 10px 24px ${colors.shadowColor}`,
      };
    }

    if (props.type === 'job') {
      const borderColor = props.status === 'failed' ? colors.error : colors.accent;
      return {
        ...baseStyles,
        background: colors.card,
        border: `2px solid ${borderColor}`,
        boxShadow: `0 0 0 1px ${borderColor}20`,
      };
    }

    // Special folder card styling
    if (props.type === 'folder') {
      return {
        ...baseStyles,
        background: selectionBg || dragOverBg || `linear-gradient(135deg, ${colors.accentBg} 0%, ${colors.card} 100%)`,
        border: selectionBorder || dragOverBorder || `1px solid ${colors.accentBorder || colors.borderLight}`,
        boxShadow: selectionShadow || `0 10px 24px ${colors.shadowColor}`,
        transform: isDragOver ? 'scale(1.02)' : undefined,
      };
    }

    return {
      ...baseStyles,
      background: selectionBg || dragOverBg || colors.card,
      border: selectionBorder || dragOverBorder || `1px solid ${colors.borderLight}`,
      boxShadow: selectionShadow || `0 10px 24px ${colors.shadowColor}`,
      transform: isDragOver ? 'scale(1.02)' : undefined,
    };
  };

  const handleMouseEnter = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onClick && !isSelectable) return;

    if (props.type === 'add') {
      e.currentTarget.style.background = colors.gradientHover;
      e.currentTarget.style.borderColor = colors.accent;
      e.currentTarget.style.borderStyle = 'solid';
    } else if (!isSelected) {
      e.currentTarget.style.background = colors.cardHover;
      if (props.type !== 'job') {
        e.currentTarget.style.borderColor = colors.borderHover;
      }
    }
    if (!isDragOver) {
      e.currentTarget.style.transform = 'translateY(-2px)';
    }
    e.currentTarget.style.boxShadow = `0 14px 30px ${colors.shadowColor}`;
  };

  const handleMouseLeave = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onClick && !isSelectable) return;

    if (props.type === 'add') {
      e.currentTarget.style.background = colors.gradientPrimary;
      e.currentTarget.style.borderColor = colors.border;
      e.currentTarget.style.borderStyle = 'dashed';
    } else if (!isSelected) {
      e.currentTarget.style.background = colors.card;
      if (props.type !== 'job') {
        e.currentTarget.style.borderColor = colors.borderLight;
      }
    }
    if (!isDragOver) {
      e.currentTarget.style.transform = 'translateY(0)';
    }
    if (!isSelected) {
      e.currentTarget.style.boxShadow = `0 10px 24px ${colors.shadowColor}`;
    }
  };

  const handleClick = (e: React.MouseEvent) => {
    if (onSelect && (e.ctrlKey || e.metaKey || isSelectable)) {
      e.preventDefault();
      e.stopPropagation();
      onSelect(e);
    } else if (onClick) {
      onClick();
    }
  };

  const handleContextMenuEvent = (e: React.MouseEvent) => {
    if (onContextMenu) {
      e.preventDefault();
      onContextMenu(e);
    }
  };

  return (
    <div
      ref={cardRef}
      data-card
      data-card-id={cardId}
      className={className}
      style={getCardStyles()}
      onClick={handleClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={handleContextMenuEvent}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      draggable={draggable && isSelected}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {/* Selection checkbox */}
      {isSelectable && (props.type === 'operator' || props.type === 'folder') && (
        <div
          style={{
            position: 'absolute',
            top: '12px',
            left: '12px',
            width: '22px',
            height: '22px',
            borderRadius: '6px',
            border: isSelected ? 'none' : `2px solid ${colors.border}`,
            background: isSelected ? colors.accent : 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all 0.2s ease-out',
            zIndex: 10,
            opacity: isSelected ? 1 : 0,
            transform: isSelected ? 'scale(1)' : 'scale(0.8)',
          }}
          onMouseEnter={(e) => {
            const parent = e.currentTarget.parentElement;
            if (parent && !isSelected) {
              e.currentTarget.style.opacity = '0.6';
              e.currentTarget.style.transform = 'scale(1)';
            }
          }}
          onMouseLeave={(e) => {
            if (!isSelected) {
              e.currentTarget.style.opacity = '0';
              e.currentTarget.style.transform = 'scale(0.8)';
            }
          }}
        >
          {isSelected && (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#ffffff"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
          )}
        </div>
      )}

      {props.type === 'add' && <AddCardContent {...props} theme={theme} />}
      {props.type === 'repo' && (
        <RepoCardContent
          {...props}
          theme={theme}
          onMenuClick={props.onMenuClick}
          showMenu={props.showMenu}
        />
      )}
      {props.type === 'operator' && (
        <OperatorCardContent
          {...props}
          theme={theme}
          onMenuClick={props.onMenuClick}
          showMenu={props.showMenu}
        />
      )}
      {props.type === 'folder' && (
        <FolderCardContent
          {...props}
          theme={theme}
          onMenuClick={props.onMenuClick}
          showMenu={props.showMenu}
        />
      )}
      {props.type === 'job' && <JobCardContent {...props} theme={theme} />}
    </div>
  );
}

function AddCardContent({ label, icon, theme }: AddCardProps & { theme: 'dark' | 'light' | 'beige' }) {
  const colors = getThemeColors(theme);
  return (
    <>
      <div style={{
        width: '56px',
        height: '56px',
        borderRadius: '14px',
        background: colors.accentBg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: '16px',
        color: colors.accent,
        transition: 'all 250ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}>
        {icon ? (
          <span style={{ fontSize: '28px' }}>{icon}</span>
        ) : (
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        )}
      </div>
      <p style={{
        color: colors.textMuted,
        fontSize: '14px',
        fontWeight: '500',
        letterSpacing: '-0.01em',
      }}>
        {label}
      </p>
    </>
  );
}

function RepoCardContent({
  name,
  path,
  researchCount,
  lastUpdated,
  theme,
  onMenuClick,
  showMenu,
  hasGraph,
  graphNodeCount,
  graphRelationshipCount,
  graphOperationStatus,
  syncEnabled
}: RepoCardProps & {
  theme: 'dark' | 'light' | 'beige';
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
}) {
  const colors = getThemeColors(theme);
  const { t, language } = useTranslation();
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString(language === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).replace(/\//g, '/');
  };

  const isOperating = !!graphOperationStatus;
  const graphStatusLabel =
    graphOperationStatus === 'queued'
      ? t('card.queuedGraph')
      : graphOperationStatus === 'generating'
        ? t('card.generatingGraph')
        : t('card.cleaningGraph');

  return (
    <>
      {/* Operation status overlay indicator */}
      {isOperating && (
        <div style={{
          position: 'absolute',
          top: '12px',
          right: '12px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 10px',
          background: colors.accentBg,
          borderRadius: '12px',
          border: `1px solid ${colors.accentBorder}`,
          zIndex: 10,
        }}>
          <div style={{
            width: '12px',
            height: '12px',
            border: `2px solid ${colors.accent}`,
            borderTopColor: 'transparent',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }} />
          <span style={{
            fontSize: '11px',
            fontWeight: '500',
            color: colors.accent,
          }}>
            {graphStatusLabel}
          </span>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flex: 1, overflow: 'hidden', paddingRight: '8px' }}>
          <h3 style={{
            fontWeight: '600',
            fontSize: '20px',
            color: colors.text,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {name}
          </h3>
          {path && (
            <span
              title={path}
              style={{
                flexShrink: 0,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '18px',
                height: '18px',
                borderRadius: '50%',
                cursor: 'help',
                color: colors.textDimmed,
                opacity: 0.5,
                transition: 'opacity 0.2s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
              onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.5'; }}
              onClick={(e) => { e.stopPropagation(); }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="16" x2="12" y2="12"/>
                <line x1="12" y1="8" x2="12.01" y2="8"/>
              </svg>
            </span>
          )}
        </div>

        {onMenuClick && (
          <button
            onClick={onMenuClick}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: '6px',
              color: colors.textMuted,
              fontSize: '18px',
              lineHeight: '1',
              transition: 'all 0.2s',
              opacity: showMenu ? 1 : 0.6
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.bgHover;
              e.currentTarget.style.opacity = '1';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.opacity = showMenu ? '1' : '0.6';
            }}
          >
            ⋯
          </button>
        )}
      </div>

      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        marginTop: 'auto'
      }}>
        {/* Research docs count - always show, even if 0 */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          color: colors.textMuted,
          fontSize: '13px',
          padding: '4px 0',
        }}>
          <span style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            background: researchCount !== undefined && researchCount > 0 ? colors.accent : colors.textDimmed,
            boxShadow: researchCount !== undefined && researchCount > 0 ? `0 0 6px ${colors.accentBorder}` : 'none',
          }} />
          <span style={{
            fontWeight: researchCount !== undefined && researchCount > 0 ? '500' : '400',
            color: researchCount !== undefined && researchCount > 0 ? colors.text : colors.textMuted,
          }}>
            {researchCount !== undefined && researchCount > 0
              ? `${researchCount} ${t('card.researchDocs')}`
              : `0 ${t('card.researchDocs')}`}
          </span>
        </div>

        {/* Knowledge Graph Status Indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          fontSize: '12px',
          color: colors.textMuted,
          padding: '4px 0',
        }}>
          {hasGraph ? (
            <>
              <span style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: colors.success,
                boxShadow: `0 0 4px ${colors.successBorder}`,
              }} />
              <span style={{ fontSize: '11px', opacity: 0.7 }}>◈</span>
              <span style={{ fontWeight: '500' }}>
                {graphNodeCount !== undefined && graphNodeCount > 0
                  ? `${graphNodeCount.toLocaleString()} ${t('card.nodes')}`
                  : t('card.graphReady')}
                {graphRelationshipCount !== undefined && graphNodeCount !== undefined && graphNodeCount > 0 && (
                  <span style={{ opacity: 0.7 }}> · {graphRelationshipCount.toLocaleString()} {t('card.edges')}</span>
                )}
              </span>
              {syncEnabled && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', marginLeft: '4px', opacity: 0.7 }}>
                  <span style={{ fontSize: '11px' }}>·</span>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="23 4 23 10 17 10"/>
                    <polyline points="1 20 1 14 7 14"/>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                  </svg>
                  <span style={{ fontSize: '11px', fontWeight: '500', color: colors.accent }}>
                    {t('card.syncEnabled') || 'Sync'}
                  </span>
                </span>
              )}
            </>
          ) : (
            <>
              <span style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background:
                  graphOperationStatus === 'queued'
                    ? (colors.info || colors.accent)
                    : graphOperationStatus === 'generating'
                      ? colors.accent
                      : colors.textDimmed,
              }} />
              <span style={{ opacity: 0.6, fontSize: '11px' }}>◇</span>
              <span style={{ opacity: 0.7 }}>
                {graphOperationStatus === 'queued'
                  ? t('card.queuedGraph')
                  : graphOperationStatus === 'generating'
                    ? t('card.generatingGraph')
                    : t('card.pendingGraph')}
              </span>
            </>
          )}
        </div>

        {/* Last updated date */}
        {lastUpdated && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: colors.textDimmed,
            fontSize: '11px',
            paddingTop: '2px',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            <span>{formatDate(lastUpdated)}</span>
          </div>
        )}
      </div>
    </>
  );
}

function OperatorCardContent({
  name,
  description,
  lastUpdated,
  referencesCount,
  codeBlocksCount,
  theme,
  onMenuClick,
  showMenu
}: OperatorCardProps & {
  theme: 'dark' | 'light' | 'beige';
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
}) {
  const colors = getThemeColors(theme);
  const { language } = useTranslation();
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString(language === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).replace(/\//g, '/');
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px' }}>
        <h3 style={{
          fontWeight: '600',
          fontSize: '18px',
          color: colors.text,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          flex: 1,
          paddingRight: '8px'
        }}>
          {name}
        </h3>

        {onMenuClick && (
          <button
            onClick={onMenuClick}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: '6px',
              color: colors.textMuted,
              fontSize: '18px',
              lineHeight: '1',
              transition: 'all 0.2s',
              opacity: showMenu ? 1 : 0.6
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.bgHover;
              e.currentTarget.style.opacity = '1';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.opacity = showMenu ? '1' : '0.6';
            }}
          >
            ⋯
          </button>
        )}
      </div>

      {description && (
        <p style={{
          color: colors.textMuted,
          fontSize: '13px',
          marginBottom: '16px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          lineHeight: '1.5',
          minHeight: '39px'
        }}>
          {description}
        </p>
      )}

      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '12px',
        marginTop: 'auto'
      }}>
        {referencesCount !== undefined && referencesCount > 0 && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: colors.textMuted,
            fontSize: '13px'
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
            <span>{referencesCount}</span>
          </div>
        )}

        {codeBlocksCount !== undefined && codeBlocksCount > 0 && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: colors.textMuted,
            fontSize: '13px'
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
              <polyline points="16 18 22 12 16 6"/>
              <polyline points="8 6 2 12 8 18"/>
            </svg>
            <span>{codeBlocksCount}</span>
          </div>
        )}

        {lastUpdated && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: colors.textDimmed,
            fontSize: '13px',
            marginLeft: 'auto'
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            <span>{formatDate(lastUpdated)}</span>
          </div>
        )}
      </div>
    </>
  );
}

function FolderCardContent({
  name,
  documentCount,
  lastUpdated,
  theme,
  onMenuClick,
  showMenu
}: FolderCardProps & {
  theme: 'dark' | 'light' | 'beige';
  onMenuClick?: (e: React.MouseEvent) => void;
  showMenu?: boolean;
}) {
  const colors = getThemeColors(theme);
  const { language } = useTranslation();
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString(language === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).replace(/\//g, '/');
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1 }}>
          {/* Folder icon */}
          <div style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: colors.accentBg,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: colors.accent,
            flexShrink: 0,
          }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
          </div>

          <h3 style={{
            fontWeight: '600',
            fontSize: '18px',
            color: colors.text,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
          }}>
            {name}
          </h3>
        </div>

        {onMenuClick && (
          <button
            onClick={onMenuClick}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: '6px',
              color: colors.textMuted,
              fontSize: '18px',
              lineHeight: '1',
              transition: 'all 0.2s',
              opacity: showMenu ? 1 : 0.6
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.bgHover;
              e.currentTarget.style.opacity = '1';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.opacity = showMenu ? '1' : '0.6';
            }}
          >
            ⋯
          </button>
        )}
      </div>

      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        marginTop: 'auto'
      }}>
        {/* Document count */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          color: colors.textMuted,
          fontSize: '13px',
          padding: '4px 0',
        }}>
          <span style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            background: documentCount !== undefined && documentCount > 0 ? colors.accent : colors.textDimmed,
            boxShadow: documentCount !== undefined && documentCount > 0 ? `0 0 6px ${colors.accentBorder}` : 'none',
          }} />
          <span style={{
            fontWeight: documentCount !== undefined && documentCount > 0 ? '500' : '400',
            color: documentCount !== undefined && documentCount > 0 ? colors.text : colors.textMuted,
          }}>
            {documentCount !== undefined && documentCount > 0
              ? `${documentCount} ${language === 'zh' ? '个文档' : 'documents'}`
              : `0 ${language === 'zh' ? '个文档' : 'documents'}`}
          </span>
        </div>

        {/* Last updated date */}
        {lastUpdated && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: colors.textDimmed,
            fontSize: '11px',
            paddingTop: '2px',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            <span>{formatDate(lastUpdated)}</span>
          </div>
        )}
      </div>
    </>
  );
}

function JobCardContent({ name, status, progress, currentStep, theme }: JobCardProps & { theme: 'dark' | 'light' | 'beige' }) {
  const colors = getThemeColors(theme);
  const { t } = useTranslation();

  const getStatusColor = () => {
    switch (status) {
      case 'failed': return colors.error;
      case 'completed': return colors.success;
      case 'stalled': return colors.accent;
      default: return colors.accent;
    }
  };

  const getStatusText = () => {
    switch (status) {
      case 'pending': return t('card.pending');
      case 'running': return t('card.running');
      case 'stalled': return 'Stalled';
      case 'completed': return t('card.completed');
      case 'failed': return t('card.failed');
    }
  };

  return (
    <>
      {/* Progress bar at top */}
      <div style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: '3px',
        background: colors.bgHover
      }}>
        <div style={{
          height: '100%',
          width: `${progress}%`,
          background: getStatusColor(),
          transition: 'width 0.3s ease'
        }} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', marginTop: '8px' }}>
        <h3 style={{
          fontWeight: '600',
          fontSize: '18px',
          color: colors.text
        }}>
          {name}
        </h3>
        <span style={{
          background: getStatusColor(),
          color: '#ffffff',
          fontSize: '10px',
          padding: '2px 6px',
          borderRadius: '4px',
          fontWeight: '500',
          display: 'flex',
          alignItems: 'center',
          gap: '4px'
        }}>
          {(status === 'running' || status === 'pending' || status === 'stalled') && (
            <div style={{
              width: '8px',
              height: '8px',
              border: '2px solid #ffffff',
              borderTopColor: 'transparent',
              borderRadius: '50%',
              animation: 'spin 0.6s linear infinite'
            }} />
          )}
          {getStatusText()}
        </span>
      </div>
      <p style={{
        color: colors.textMuted,
        fontSize: '14px',
        marginBottom: '12px'
      }}>
        {currentStep || t('card.initializing')}
      </p>
      <p style={{
        color: colors.textDimmed,
        fontSize: '14px'
      }}>
        {t('card.progress')}: {progress}%
      </p>
    </>
  );
}
