'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { CSSProperties } from 'react';

interface SkeletonProps {
  width?: string;
  height?: string;
  className?: string;
  borderRadius?: string;
}

export function Skeleton({ 
  width = '100%', 
  height = '20px',
  className = '',
  borderRadius = '8px'
}: SkeletonProps) {
  const style: CSSProperties = {
    width,
    height,
    borderRadius,
    background: 'linear-gradient(90deg, #1a202c 0%, #2d3748 50%, #1a202c 100%)',
    backgroundSize: '200% 100%',
    animation: 'shimmer 2s infinite linear',
  };

  return <div className={className} style={style} />;
}

export function ChatMessageSkeleton() {
  return (
    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <Skeleton width="40px" height="40px" borderRadius="50%" />
        <Skeleton width="120px" height="16px" />
      </div>
      <Skeleton width="100%" height="60px" />
      <Skeleton width="80%" height="40px" />
    </div>
  );
}

export function CodeBlockSkeleton() {
  return (
    <div style={{ 
      padding: '20px', 
      background: '#0d1117', 
      borderRadius: '12px',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px'
    }}>
      {[1, 2, 3, 4, 5].map(i => (
        <Skeleton 
          key={i} 
          width={`${60 + Math.random() * 40}%`} 
          height="16px" 
        />
      ))}
    </div>
  );
}

export function RepoCardSkeleton() {
  return (
    <div style={{
      background: '#1a202c',
      borderRadius: '16px',
      padding: '24px',
      border: '1px solid #2d3748',
      minHeight: '160px',
      display: 'flex',
      flexDirection: 'column',
      gap: '12px'
    }}>
      <Skeleton width="60%" height="24px" />
      <Skeleton width="40%" height="16px" />
      <Skeleton width="50%" height="16px" />
    </div>
  );
}

export function ChatListSkeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {[1, 2, 3].map(i => (
        <ChatMessageSkeleton key={i} />
      ))}
    </div>
  );
}
